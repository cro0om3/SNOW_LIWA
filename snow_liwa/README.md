# SNOW LIWA – Booking & Payment App

This is a Streamlit app for **SNOW LIWA**:

- Landing page with background from your PDF.
- Pages:
  - Welcome & Ticket booking
  - Who we are
  - Experience
  - Contact
  - Admin Dashboard
- Payment:
  - Uses **Ziina Payment Intent API**.
  - Creates a payment intent and redirects user to Ziina.
  - After payment, Ziina redirects back to the app with `result` and `pi_id` in the URL.
- Data:
  - Stores bookings in `data/bookings.xlsx`.

## Setup

1. Create a virtual environment (optional).
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Put your background image here:

   ```text
   assets/snow_liwa_bg.jpg
   ```

4. Configure Ziina secrets in `.streamlit/secrets.toml`.

5. Run the app:

   ```bash
   streamlit run app.py
   ```
