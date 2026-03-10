# Claire's Farm Eggs Prototype

Local-first prototype for Claire's egg sales website. The app manages inventory, first-come-first-served orders, cash reservations, card checkout pricing, weekly pickup rules, news posts, and contact messages.

## Features

- Public pages for home, orders, news, and contact
- Admin dashboard for inventory, order status, and news publishing
- SQLite storage with seeded demo inventory and a sample announcement
- Wednesday pickup scheduling for Crawford, Colorado from 3:00 PM to 4:00 PM America/Denver
- Cash reservations and card orders with a 10% card processing fee
- Stripe-ready payment adapter with offline demo mode
- Facebook publishing queue with offline demo sync

## Run locally

1. Create a virtual environment:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   py -3.11 -m pip install -r requirements.txt
   ```

3. Optional: copy `.env.example` values into your shell.

4. Start the app:

   ```powershell
   py -3.11 -m app
   ```

5. Open `http://127.0.0.1:5000`.

The app now targets Python 3.8+ so it can run on Gandi Web Hosting, while still working locally on newer Python versions.

## Admin login

- URL: `http://127.0.0.1:5000/admin/login`
- Default password: `claire-eggs-demo`

Change `CLAIRE_ADMIN_PASSWORD` before using this beyond local development.

## Environment

See `.env.example` for available settings.

## Deploy on Render

This app is prepared for a Render web service with a persistent disk mounted at `/var/data`.

1. Push this project to a Git repository.
2. In Render, create a new Blueprint and point it at the repo.
3. Render will read `render.yaml` and create a Python web service named `claires-eggs`.
4. Set a strong `CLAIRE_ADMIN_PASSWORD`.
5. After Render assigns the public URL, set `SITE_URL` to that exact HTTPS address.
6. If you want live card payments, change `PAYMENT_MODE` to `stripe` and add Stripe keys.

Important:

- The current deployment target assumes a single instance with SQLite on a persistent disk.
- This is acceptable for an initial low-traffic launch, but the long-term production path should move orders and inventory into Postgres.

## Stripe and Facebook

- `PAYMENT_MODE=demo` keeps the card flow local and marks card orders as paid in demo mode.
- If you later add Stripe keys and switch to `PAYMENT_MODE=stripe`, the app will create a Stripe Checkout session.
- `FACEBOOK_SYNC_MODE=demo` records a simulated Facebook publish locally. Real group posting will require separate Facebook app permissions and may need a different integration path.
