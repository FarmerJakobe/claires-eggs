from __future__ import annotations

from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import (
    Flask,
    abort,
    g,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from .config import card_payments_enabled, load_config
from .db import get_db, init_app as init_db
from .payments import parse_stripe_webhook, refresh_payment_from_session
from .schedule import local_now, next_pickup_window
from .store import (
    create_expense_receipt,
    StoreError,
    create_sales_entry,
    create_inventory_item,
    delete_contact_message,
    get_expense_receipt,
    get_financial_summary,
    get_order,
    get_post,
    get_post_by_slug,
    get_sales_entry,
    list_active_inventory,
    list_all_inventory,
    list_contact_messages,
    list_expense_receipts,
    list_order_items,
    list_popular_pages,
    list_posts,
    list_recent_orders,
    list_sales_entries,
    list_visit_daily_totals,
    place_order,
    record_website_visit,
    save_contact_message,
    save_post,
    sync_post_to_facebook,
    update_sales_entry,
    update_inventory_item,
    update_order_payment,
    update_order_status,
)
from .utils import cents_to_dollars, dollars_to_cents


PUBLIC_VISIT_PATH_PREFIXES = ("/admin", "/static", "/healthz", "/webhooks")
ALLOWED_RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".pdf", ".png", ".webp"}
ALLOWED_POST_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(load_config())
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    init_db(app)

    app.jinja_env.filters["money"] = cents_to_dollars

    @app.context_processor
    def inject_site_context():
        return {
            "pickup_window": next_pickup_window(),
            "payment_mode": app.config["PAYMENT_MODE"],
            "card_payments_enabled": card_payments_enabled(app.config),
        }

    @app.before_request
    def prepare_visitor_tracking():
        is_public_get = (
            request.method == "GET"
            and not any(request.path.startswith(prefix) for prefix in PUBLIC_VISIT_PATH_PREFIXES)
        )
        g.track_visitor_event = is_public_get
        if not is_public_get:
            return
        g.visitor_token = request.cookies.get("claire_visitor") or uuid4().hex
        g.set_visitor_cookie = "claire_visitor" not in request.cookies

    @app.after_request
    def persist_visitor_tracking(response):
        if getattr(g, "track_visitor_event", False) and response.status_code < 400:
            database = get_db()
            try:
                record_website_visit(database, request.path, g.visitor_token)
                database.commit()
            except Exception:
                database.rollback()
        if getattr(g, "set_visitor_cookie", False):
            response.set_cookie(
                "claire_visitor",
                g.visitor_token,
                max_age=60 * 60 * 24 * 365,
                samesite="Lax",
                secure=request.is_secure,
                httponly=True,
            )
        return response

    @app.template_filter("nl2br")
    def nl2br(value: str) -> str:
        paragraphs = [segment.strip() for segment in value.splitlines() if segment.strip()]
        return "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)

    @app.template_filter("pretty_date")
    def pretty_date(value: str) -> str:
        try:
            if not value:
                return ""
            return datetime.fromisoformat(value[:10]).strftime("%b %d, %Y")
        except (TypeError, ValueError):
            return ""

    def admin_required(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not session.get("is_admin"):
                return redirect(url_for("admin_login"))
            return view_func(*args, **kwargs)

        return wrapped

    @app.route("/")
    def home():
        database = get_db()
        inventory = list_active_inventory(database)
        posts = list_posts(database, published_only=True)[:3]
        total_cartons = sum(item["quantity_available"] for item in inventory)
        return render_template(
            "home.html",
            inventory=inventory,
            posts=posts,
            total_cartons=total_cartons,
        )

    @app.route("/healthz")
    def healthz():
        database = get_db()
        database.execute("SELECT 1").fetchone()
        return {"status": "ok"}, 200

    @app.route("/media/posts/<stored_name>")
    def post_image_file(stored_name: str):
        return send_from_directory(
            app.config["POSTS_UPLOAD_DIR"],
            stored_name,
            as_attachment=False,
        )

    @app.route("/orders", methods=["GET", "POST"])
    def orders():
        database = get_db()
        inventory = list_active_inventory(database)
        if request.method == "POST":
            try:
                order_id = place_order(database, request.form.to_dict())
            except StoreError as exc:
                flash(str(exc), "error")
            except ValueError:
                flash("Please enter valid quantities for each item.", "error")
            else:
                return redirect(url_for("order_confirmation", order_id=order_id))

        return render_template("orders.html", inventory=inventory)

    @app.route("/orders/<int:order_id>/confirmation")
    def order_confirmation(order_id: int):
        database = get_db()
        session_id = request.args.get("session_id", "").strip()
        if session_id:
            try:
                session_update = refresh_payment_from_session(session_id, app.config)
            except Exception:
                database.rollback()
            else:
                if session_update and session_update.order_id == order_id:
                    update_order_payment(
                        database,
                        order_id,
                        session_update.payment_status,
                        session_update.stripe_reference,
                        "" if session_update.payment_status == "paid_online" else session_update.checkout_url,
                    )
                    database.commit()

        order_bundle = get_order(database, order_id)
        if not order_bundle:
            abort(404)
        return render_template("order_confirmation.html", **order_bundle)

    @app.route("/webhooks/stripe", methods=["POST"])
    def stripe_webhook():
        database = get_db()
        signature = request.headers.get("Stripe-Signature", "")
        try:
            session_update = parse_stripe_webhook(request.get_data(), signature, app.config)
        except Exception:
            database.rollback()
            return {"error": "invalid webhook"}, 400

        if not session_update:
            return {"status": "ignored"}, 200

        try:
            update_order_payment(
                database,
                session_update.order_id,
                session_update.payment_status,
                session_update.stripe_reference,
                "" if session_update.payment_status == "paid_online" else session_update.checkout_url,
            )
        except StoreError:
            database.rollback()
            return {"error": "order not found"}, 404

        database.commit()
        return {"status": "ok"}, 200

    @app.route("/news")
    def news():
        database = get_db()
        posts = list_posts(database, published_only=True)
        return render_template("news.html", posts=posts)

    @app.route("/news/<slug>")
    def news_detail(slug: str):
        database = get_db()
        post = get_post_by_slug(database, slug)
        if not post:
            abort(404)
        return render_template("news_detail.html", post=post)

    @app.route("/contact", methods=["GET", "POST"])
    def contact():
        if request.method == "POST":
            database = get_db()
            try:
                save_contact_message(database, request.form.to_dict())
                database.commit()
            except Exception:
                database.rollback()
                flash("We could not save your message. Try again.", "error")
            else:
                flash("Thanks. Claire will see your message on the admin dashboard.", "success")
                return redirect(url_for("contact"))

        return render_template("contact.html")

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "POST":
            password = request.form.get("password", "")
            if password == app.config["ADMIN_PASSWORD"]:
                session["is_admin"] = True
                flash("Admin access granted.", "success")
                return redirect(url_for("admin_dashboard"))
            flash("Incorrect password.", "error")
        return render_template("admin/login.html")

    @app.route("/admin/logout", methods=["POST"])
    @admin_required
    def admin_logout():
        session.clear()
        flash("Signed out.", "success")
        return redirect(url_for("home"))

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        database = get_db()
        orders = list_recent_orders(database)
        order_items = list_order_items(database, [order["id"] for order in orders])
        inventory = list_all_inventory(database)
        posts = list_posts(database)
        messages = list_contact_messages(database)
        visit_days = list_visit_daily_totals(database, days=14)
        visit_chart_max = max(
            [max(day["page_views"], day["unique_visitors"]) for day in visit_days] or [0]
        )
        return render_template(
            "admin/dashboard.html",
            orders=orders,
            order_items=order_items,
            inventory=inventory,
            posts=posts,
            messages=messages,
            financial_summary=get_financial_summary(database),
            sales_entries=list_sales_entries(database),
            expense_receipts=list_expense_receipts(database),
            visit_days=visit_days,
            visit_chart_max=visit_chart_max,
            popular_pages=list_popular_pages(database),
            receipt_upload_accept=",".join(sorted(ALLOWED_RECEIPT_EXTENSIONS)),
            post_upload_accept=",".join(sorted(ALLOWED_POST_IMAGE_EXTENSIONS)),
            today=local_now().date().isoformat(),
        )

    @app.route("/admin/inventory/new", methods=["GET", "POST"])
    @admin_required
    def admin_inventory_new():
        if request.method == "POST":
            database = get_db()
            form_data = normalize_form(request.form)
            try:
                create_inventory_item(database, form_data)
            except StoreError as exc:
                database.rollback()
                flash(str(exc), "error")
            except Exception:
                database.rollback()
                flash("We could not create that inventory item.", "error")
            else:
                database.commit()
                flash("Inventory item created.", "success")
                return redirect(url_for("admin_dashboard"))
        return render_template("admin/inventory_form.html", item=None)

    @app.route("/admin/inventory/<int:item_id>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_inventory_edit(item_id: int):
        database = get_db()
        item = database.execute(
            "SELECT * FROM inventory_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not item:
            abort(404)
        if request.method == "POST":
            form_data = normalize_form(request.form)
            reason = form_data.get("stock_reason") or "Admin stock update"
            try:
                update_inventory_item(database, item_id, form_data, reason)
            except StoreError as exc:
                database.rollback()
                flash(str(exc), "error")
            else:
                database.commit()
                flash("Inventory item updated.", "success")
                return redirect(url_for("admin_dashboard"))
        return render_template("admin/inventory_form.html", item=item)

    @app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
    @admin_required
    def admin_order_status(order_id: int):
        database = get_db()
        new_status = request.form.get("order_status", "open")
        try:
            update_order_status(database, order_id, new_status)
        except StoreError as exc:
            database.rollback()
            flash(str(exc), "error")
        else:
            database.commit()
            if new_status == "cancelled":
                flash(f"Order #{order_id} cancelled and stock returned to inventory.", "success")
            else:
                flash(f"Order #{order_id} updated to {new_status}.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/sales/new", methods=["POST"])
    @admin_required
    def admin_sales_new():
        database = get_db()
        form_data = normalize_form(request.form)
        try:
            form_data["amount_cents"] = dollars_to_cents(form_data.get("amount", ""))
            create_sales_entry(database, form_data)
        except (StoreError, ValueError) as exc:
            database.rollback()
            flash(str(exc), "error")
        else:
            database.commit()
            flash("Sale logged.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/sales/<int:sale_id>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_sales_edit(sale_id: int):
        database = get_db()
        sale = get_sales_entry(database, sale_id)
        if not sale:
            abort(404)

        if request.method == "POST":
            form_data = normalize_form(request.form)
            try:
                form_data["amount_cents"] = dollars_to_cents(form_data.get("amount", ""))
                update_sales_entry(database, sale_id, form_data)
            except (StoreError, ValueError) as exc:
                database.rollback()
                flash(str(exc), "error")
            else:
                database.commit()
                flash("Sale entry updated.", "success")
                return redirect(url_for("admin_dashboard"))
            sale = get_sales_entry(database, sale_id)

        return render_template("admin/sales_form.html", sale=sale)

    @app.route("/admin/expenses/new", methods=["POST"])
    @admin_required
    def admin_expenses_new():
        database = get_db()
        form_data = normalize_form(request.form)
        receipt_file = request.files.get("receipt_file")
        stored_name = None
        try:
            form_data["amount_cents"] = dollars_to_cents(form_data.get("amount", ""))
            if receipt_file and receipt_file.filename:
                stored_name = save_receipt_upload(receipt_file, app.config["RECEIPTS_UPLOAD_DIR"])
                form_data["receipt_original_name"] = receipt_file.filename
                form_data["receipt_stored_name"] = stored_name
                form_data["receipt_content_type"] = receipt_file.content_type or ""
            create_expense_receipt(database, form_data)
        except (StoreError, ValueError) as exc:
            database.rollback()
            if stored_name:
                remove_receipt_upload(app.config["RECEIPTS_UPLOAD_DIR"], stored_name)
            flash(str(exc), "error")
        except Exception:
            database.rollback()
            if stored_name:
                remove_receipt_upload(app.config["RECEIPTS_UPLOAD_DIR"], stored_name)
            flash("We could not save that expense receipt.", "error")
        else:
            database.commit()
            flash("Expense saved.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/expenses/<int:expense_id>/receipt")
    @admin_required
    def admin_expense_receipt_file(expense_id: int):
        database = get_db()
        expense = get_expense_receipt(database, expense_id)
        if not expense or not expense["receipt_stored_name"]:
            abort(404)
        return send_from_directory(
            app.config["RECEIPTS_UPLOAD_DIR"],
            expense["receipt_stored_name"],
            as_attachment=False,
            download_name=expense["receipt_original_name"] or expense["receipt_stored_name"],
            mimetype=expense["receipt_content_type"] or None,
        )

    @app.route("/admin/notices/new", methods=["POST"])
    @admin_required
    def admin_notice_new():
        database = get_db()
        notice_image = request.files.get("notice_image")
        stored_name = None
        try:
            form_data = build_notice_form_data(request.form)
            if notice_image and notice_image.filename:
                stored_name = save_post_image(
                    notice_image, app.config["POSTS_UPLOAD_DIR"]
                )
                form_data["image_original_name"] = notice_image.filename
                form_data["image_stored_name"] = stored_name
                form_data["image_content_type"] = notice_image.content_type or ""
            save_post(database, form_data)
        except (StoreError, ValueError) as exc:
            database.rollback()
            if stored_name:
                remove_post_image(app.config["POSTS_UPLOAD_DIR"], stored_name)
            flash(str(exc), "error")
        except Exception:
            database.rollback()
            if stored_name:
                remove_post_image(app.config["POSTS_UPLOAD_DIR"], stored_name)
            flash("We could not save that notice.", "error")
        else:
            database.commit()
            flash("Notice board post published.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/news/new", methods=["GET", "POST"])
    @admin_required
    def admin_news_new():
        if request.method == "POST":
            database = get_db()
            image_file = request.files.get("image_file")
            stored_name = None
            try:
                form_data = normalize_form(request.form)
                if image_file and image_file.filename:
                    stored_name = save_post_image(
                        image_file, app.config["POSTS_UPLOAD_DIR"]
                    )
                    form_data["image_original_name"] = image_file.filename
                    form_data["image_stored_name"] = stored_name
                    form_data["image_content_type"] = image_file.content_type or ""
                save_post(database, form_data)
            except StoreError as exc:
                database.rollback()
                if stored_name:
                    remove_post_image(app.config["POSTS_UPLOAD_DIR"], stored_name)
                flash(str(exc), "error")
            except ValueError as exc:
                database.rollback()
                if stored_name:
                    remove_post_image(app.config["POSTS_UPLOAD_DIR"], stored_name)
                flash(str(exc), "error")
            else:
                database.commit()
                flash("Post saved.", "success")
                return redirect(url_for("admin_dashboard"))
        return render_template("admin/news_form.html", post=None)

    @app.route("/admin/news/<int:post_id>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_news_edit(post_id: int):
        database = get_db()
        post = get_post(database, post_id)
        if not post:
            abort(404)
        if request.method == "POST":
            image_file = request.files.get("image_file")
            stored_name = None
            try:
                form_data = normalize_form(request.form)
                if image_file and image_file.filename:
                    stored_name = save_post_image(
                        image_file, app.config["POSTS_UPLOAD_DIR"]
                    )
                    form_data["image_original_name"] = image_file.filename
                    form_data["image_stored_name"] = stored_name
                    form_data["image_content_type"] = image_file.content_type or ""
                save_post(database, form_data, post_id=post_id)
            except StoreError as exc:
                database.rollback()
                if stored_name:
                    remove_post_image(app.config["POSTS_UPLOAD_DIR"], stored_name)
                flash(str(exc), "error")
            except ValueError as exc:
                database.rollback()
                if stored_name:
                    remove_post_image(app.config["POSTS_UPLOAD_DIR"], stored_name)
                flash(str(exc), "error")
            else:
                database.commit()
                flash("Post updated.", "success")
                return redirect(url_for("admin_dashboard"))
        return render_template("admin/news_form.html", post=post)

    @app.route("/admin/news/<int:post_id>/facebook", methods=["POST"])
    @admin_required
    def admin_news_facebook(post_id: int):
        database = get_db()
        try:
            sync_post_to_facebook(database, post_id)
        except StoreError as exc:
            database.rollback()
            flash(str(exc), "error")
        else:
            database.commit()
            flash("Facebook sync action recorded.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/messages/<int:message_id>/delete", methods=["POST"])
    @admin_required
    def admin_message_delete(message_id: int):
        database = get_db()
        try:
            delete_contact_message(database, message_id)
        except StoreError as exc:
            database.rollback()
            flash(str(exc), "error")
        else:
            database.commit()
            flash("Message deleted.", "success")
        return redirect(url_for("admin_dashboard"))

    return app


def normalize_form(form_data):
    normalized = {}
    for key in form_data.keys():
        values = form_data.getlist(key)
        if len(values) == 1:
            normalized[key] = values[0]
        else:
            normalized[key] = values
    return normalized


def save_receipt_upload(receipt_file, upload_dir: str) -> str:
    original_name = secure_filename(receipt_file.filename or "")
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_RECEIPT_EXTENSIONS:
        raise ValueError("Receipt files must be PNG, JPG, WEBP, or PDF.")

    target_dir = Path(upload_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{extension}"
    receipt_file.save(target_dir / stored_name)
    return stored_name


def remove_receipt_upload(upload_dir: str, stored_name: str) -> None:
    target_path = Path(upload_dir) / stored_name
    if target_path.exists():
        target_path.unlink()


def save_post_image(image_file, upload_dir: str) -> str:
    original_name = secure_filename(image_file.filename or "")
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_POST_IMAGE_EXTENSIONS:
        raise ValueError("Notice images must be PNG, JPG, or WEBP.")

    target_dir = Path(upload_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{extension}"
    image_file.save(target_dir / stored_name)
    return stored_name


def remove_post_image(upload_dir: str, stored_name: str) -> None:
    target_path = Path(upload_dir) / stored_name
    if target_path.exists():
        target_path.unlink()


def build_notice_form_data(form_data):
    message = form_data.get("message", "").strip()
    if not message:
        raise ValueError("Notice message is required.")

    title = message.splitlines()[0].strip()[:72]
    if len(message.splitlines()[0].strip()) > 72:
        title = f"{title.rstrip()}..."

    excerpt = message.replace("\r", " ").replace("\n", " ").strip()[:160]
    return {
        "title": title or "Farm notice",
        "excerpt": excerpt or message[:160],
        "body": message,
        "is_published": "1",
        "publish_to_facebook": "1" if form_data.get("publish_to_facebook") else "",
        "facebook_message": message,
    }
