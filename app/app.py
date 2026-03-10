from __future__ import annotations

from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import card_payments_enabled, load_config
from .db import get_db, init_app as init_db
from .schedule import next_pickup_window
from .store import (
    StoreError,
    create_inventory_item,
    get_order,
    get_post,
    get_post_by_slug,
    list_active_inventory,
    list_all_inventory,
    list_contact_messages,
    list_order_items,
    list_posts,
    list_recent_orders,
    place_order,
    save_contact_message,
    save_post,
    sync_post_to_facebook,
    update_inventory_item,
    update_order_status,
)
from .utils import cents_to_dollars


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

    @app.template_filter("nl2br")
    def nl2br(value: str) -> str:
        paragraphs = [segment.strip() for segment in value.splitlines() if segment.strip()]
        return "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)

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
        order_bundle = get_order(database, order_id)
        if not order_bundle:
            abort(404)
        return render_template("order_confirmation.html", **order_bundle)

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
        return render_template(
            "admin/dashboard.html",
            orders=orders,
            order_items=order_items,
            inventory=inventory,
            posts=posts,
            messages=messages,
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
            flash(f"Order #{order_id} updated to {new_status}.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/news/new", methods=["GET", "POST"])
    @admin_required
    def admin_news_new():
        if request.method == "POST":
            database = get_db()
            form_data = normalize_form(request.form)
            try:
                save_post(database, form_data)
            except StoreError as exc:
                database.rollback()
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
            form_data = normalize_form(request.form)
            try:
                save_post(database, form_data, post_id=post_id)
            except StoreError as exc:
                database.rollback()
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
