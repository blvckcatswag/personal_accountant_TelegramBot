# Data Model

Core entities:

- `users`: Telegram user profile, language, base currency, plan.
- `receipts`: normalized transaction header, OCR confidence, original and converted amounts.
- `receipt_items`: line items with category, quantity and price.
- `categories`: system and user-facing taxonomy for analytics.
- `user_category_rules`: personalized matching rules for categorization.
- `budgets`: weekly or monthly spending limits.
- `notifications`: scheduled and delivered alerts.
- `currency_rates`: cached historical conversion rates.

