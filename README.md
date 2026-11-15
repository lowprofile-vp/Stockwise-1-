# StockWise

StockWise is an inventory intelligence and restocking optimization system for small businesses, grocery stores, and college canteens. It records stock, sales, consumption, and wastage, estimates when products will run out, ranks what to buy first, and allocates a limited restocking budget.

The ranking and budget choices are calculated on the Flask backend with custom data structures and algorithms. PostgreSQL is the source of truth. The React app displays those results and never invents dashboard numbers.

## Problem

Shops often reorder from the quantity on the shelf. That misses how fast a product is used, how long the supplier takes, whether demand is rising, and how much money is available this week. Rice with 18 kg left can be more urgent than a slow product with 5 kg left when the canteen uses 6 kg a day and the supplier needs 4 days.

## Solution

For every active product StockWise computes:

- average daily demand from sales and consumption
- days until stockout (`current stock ÷ average daily demand`)
- a reorder point that includes supplier lead time and a 2-day safety buffer
- a suggested order quantity and its estimated cost
- an urgency band: Critical, High, Medium, or Low

A custom min-heap returns the most urgent product first. A greedy scan and a 0/1 knapsack then choose which whole restock lots fit a budget. Nothing is ordered until an owner approves a purchase order. Receiving that order is what increases stock, and the increase is written as a receipt transaction.

## Features

- Landing, registration, login, forgot-password, and first-time workspace setup
- JWT authentication, hashed passwords, logout token blocklist, and role checks
- Isolated business workspaces. Staff join with a workspace code
- Owner: products, suppliers, users, settings, budgets, and purchase-order approval
- Staff: dashboard, inventory, stock movements, supplier directory, recommendations, simulations, and reports
- Overview cards, alerts, demand charts, top restock priorities, and recent activity
- Inventory search, filters, sorts, pagination, product history, and deactivation
- Sales, consumption, wastage, adjustments, daily summaries, and CSV import
- Restock recommendations with an explicit formula
- Budget planner that compares greedy selection with dynamic programming
- Purchase orders: draft, approved, ordered, partially received, received, cancelled
- Supplier directory, price comparison, and delivery performance
- Moving-average forecasts and demand-trend lists
- What-if simulations for a demand spike, a supplier delay, and a smaller budget
- Nine reports with CSV export

## Architecture

```
Browser (React)
    → Flask REST API
        → business services (inventory, stock, planning, optimizer, simulations, reports)
            → algorithms (hash map, heap, sliding window, sorts, queue, greedy, knapsack)
            → PostgreSQL through SQLAlchemy
```

| Layer | Location |
| --- | --- |
| Presentation | `frontend/` |
| HTTP API | `backend/app/routes/` |
| Business logic | `backend/app/services/` |
| DSA | `backend/app/algorithms/` |
| Data | `backend/app/models.py` |

Custom in-memory structures are rebuilt from database rows whenever recommendations, sorts, or imports run. They are not a second database.

## Technology stack

- Frontend: React, Vite, JavaScript, Tailwind CSS, Recharts, React Router, Axios, Lucide React
- Backend: Python, Flask, Flask-CORS, Flask-JWT-Extended, SQLAlchemy, Flask-Migrate, Werkzeug password hashing
- Database: PostgreSQL (`psycopg`). Tests can use SQLite
- Deployment targets: Vercel (frontend), Render (API), hosted PostgreSQL

## Database schema

Money columns are `Numeric`. Stock quantities are `Numeric` and cannot be negative. Every business-owned row carries `business_id`.

| Table | Role |
| --- | --- |
| `users` | Name, email, password hash, role (`admin` or `staff`), business |
| `businesses` | Name, type, currency, owner, restock budget, join code, setup flag |
| `categories` | Configurable product categories per business |
| `products` | Stock, minimum, unit, prices, preferred supplier, active flag |
| `suppliers` | Contact details and average delivery days |
| `product_suppliers` | Many-to-many price and minimum order quantity |
| `transactions` | Sale, consumption, wastage, adjustment, receipt, opening, with `stock_after` |
| `purchase_orders` | Status, totals, expected and received dates |
| `purchase_order_items` | Ordered quantity, received quantity, unit cost |
| `restock_recommendations` | Snapshot written when recommendations are generated |
| `simulation_runs` | Scenario type, inputs, and results as JSON |
| `token_blocklist` | Revoked JWT identifiers |
| `password_reset_tokens` | Hashed reset tokens |

`businesses.owner_id` is an indexed integer without a database foreign key. A foreign key would cycle with `users.business_id`. Both values are set in the same registration transaction.

Stock changes go through one service that locks the product row, rejects a negative result, and inserts the transaction in the same database transaction. Editing a product cannot silently overwrite `current_stock`. Deleting a product deactivates it so history remains.

## DSA concepts

All of these live in `backend/app/algorithms/` and are called by the services. Comments in each file describe the operations.

### Hash map — `inventory_map.py`

Separate chaining. The hash is a polynomial rolling hash (`× 131`), not Python's `dict`. The map resizes when the load factor passes 0.75. Product lookup and grouping of transactions by product id use it.

- Average insert, get, delete: O(1)
- Worst case (every key in one bucket): O(n)
- Space: O(n)

### Min-heap — `priority_queue.py`

Binary min-heap with `insert`, `peek`, `extract`, `heapify`, and `is_empty`. Entries are `(priority, sequence, item)` so equal priorities do not compare dictionaries. Restock ranking uses priority `(-urgency score, days until stockout or 9999)`. The smallest priority is the most urgent product.

- Insert and extract: O(log n)
- Peek: O(1)
- Heapify: O(n)
- Space: O(n)

### Sliding window — `sliding_window.py`

A running sum adds the newest day and drops the day that leaves a fixed window. Calendar days with no movement are zeros, so quiet days lower the average. The 7-day average is used for planning when at least 7 days exist. The 30-day average is reported only when 30 days exist. Shorter histories are labeled as weak estimates.

- One pass: O(n) time, O(1) extra memory

### Sorting — `sorting.py`

Merge sort ranks names and greedy value densities. It is stable, so equal densities keep urgency order. Quick sort (median-of-three pivot) ranks quantity and price. Neither sort calls `list.sort`.

- Merge sort: O(n log n) time, O(n) space, stable
- Quick sort: O(n log n) average, O(n²) worst, O(log n) average stack space

### Queue — `queue.py`

A FIFO list with a head index. CSV import validates every row, enqueues the operations, then dequeues them in order inside one database transaction. A failure rolls the batch back.

- Enqueue, dequeue, peek: O(1) amortized
- Space: O(n)

### Greedy selection — `greedy.py`

Lots are ranked by urgency score ÷ cost, then taken while the full cost fits. Lots above the original budget are excluded immediately. This is a heuristic: it can miss the combination with the highest total score.

- Time: O(n log n) for the sort, then O(n)
- Space: O(n)

### 0/1 knapsack — `knapsack.py`

Weight is the lot cost in whole currency units. Value is the integer urgency score. Capacity is the budget in the same units. The recurrence is:

```
dp[i][w] = max(dp[i-1][w], dp[i-1][w - w_i] + v_i)  when w_i <= w
```

Backtracking from `dp[n][W]` recovers the selected lots. A lot is taken entirely or not at all, which matches approving a recommended order. Budgets above 100,000 currency units are scaled so the table stays bounded, then checked against the original costs. A normal canteen budget does not hit that guard.

- Time: O(n × W)
- Space: O(n × W) for the full table used to recover the selection

The planner states this plainly: greedy is the fast heuristic, and dynamic programming is optimal for this 0/1 model. Maximizing total urgency score can leave out one expensive critical lot when several smaller lots score higher together.

## Urgency formula

Defined in `backend/app/services/planning.py`.

```
reorder point   = daily demand × (lead time + 2 safety days)
order-up-to     = daily demand × (lead time + 7 review days + 2 safety days)
suggested qty   = max(0, order-up-to − current stock), then at least the supplier MOQ
days of cover   = current stock ÷ daily demand
```

| Band | Rule |
| --- | --- |
| Critical | Out of stock, or days of cover ≤ supplier lead time |
| High | Cover reaches the lead time plus the 2-day safety buffer, but not only the lead time |
| Medium | Stock is at or below the minimum, or cover is within three lead times |
| Low | Otherwise |

Demand trend can nudge the numeric score. It does not change the band. The score is the knapsack value. Stock status (Healthy, Low Stock, Critical, Out of Stock) is separate from urgency: a large quantity can still be urgent if it will not last through delivery.

If a product has no supplier, lead time defaults to 3 days and the reason says so. These numbers are estimates from recent usage, not a forecast of future demand.

Example: Rice, 18 kg on hand, 6 kg a day, 4-day lead. Cover is 3 days, which is inside the lead time, so the product is Critical.

## Project layout

```
StockWise/
├── README.md
├── docker-compose.yml
├── .gitignore
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   ├── Procfile
│   ├── pytest.ini
│   ├── run.py
│   ├── manage.py
│   ├── seed.py
│   ├── app/
│   │   ├── algorithms/
│   │   ├── routes/
│   │   ├── services/
│   │   ├── models.py
│   │   └── config.py
│   └── tests/
└── frontend/
    ├── package.json
    ├── .env.example
    ├── vercel.json
    └── src/
        ├── components/
        ├── pages/
        ├── context/
        └── services/
```

## Environment variables

Copy the examples and edit them. Do not commit real secrets.

Backend (`backend/.env`, from `backend/.env.example`):

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Project URL. With the publishable key, sign-in and data both use Supabase |
| `SUPABASE_PUBLISHABLE_KEY` | Publishable key from Project Settings → API. The browser may use it. Row Level Security still applies |
| `JWT_SECRET_KEY` | Signing key for the built-in login. Required, and must be long, when `FLASK_ENV=production` and Supabase is not configured |
| `SUPABASE_JWT_SECRET` | Optional. Legacy HS256 JWT secret for older Supabase projects |
| `SECRET_KEY` | Flask secret |
| `FLASK_ENV` | `development` or `production` |
| `FLASK_DEBUG` | `1` locally |
| `PORT` | API port, default 5000 |
| `CORS_ORIGINS` | Browser origins allowed to call the API |
| `FRONTEND_URL` | Used in password-reset links |
| `RETURN_RESET_TOKEN` | `1` returns a dev reset URL when email is not sent. Set `0` in production |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Optional email for password reset |

Frontend (`frontend/.env`, from `frontend/.env.example`):

| Variable | Purpose |
| --- | --- |
| `VITE_API_URL` | API base, default `http://localhost:5000/api` |
| `VITE_SUPABASE_URL` | Same project URL as `SUPABASE_URL`. Leave empty to use the built-in login |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Same publishable key as `SUPABASE_PUBLISHABLE_KEY` |

## Supabase Auth and database

Supabase Auth stores passwords. The Supabase Data API stores the workspace, and Row Level Security limits each request to the signed-in user's business. StockWise still stores the role and the business on `public.users`, matched by `supabase_uid`.

The API and the browser use only the project URL and the publishable key. The user's access token is sent with every Data API call, so Postgres treats the caller as `authenticated` and the policies in `backend/supabase_schema.sql` apply. A database password is not required.

1. Create a project at [supabase.com](https://supabase.com).
2. In Authentication → Providers, keep Email enabled. For a local demo, turn off Confirm email so registration returns a session immediately.
3. In Authentication → URL configuration, add `http://localhost:5173/reset-password` to the redirect allow list.
4. Copy the project URL and the publishable key into `backend/.env` as `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`, and into `frontend/.env` as `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`.
5. Open `backend/supabase_schema.sql`, paste it into the Supabase SQL Editor, and run it.

Register in the app. The owner gets a workspace code. Staff register themselves and enter that code. Their password stays in Supabase Auth.

Leave both Supabase variables empty to keep the built-in login and the local SQLite file. The automated tests use that path.

## Database setup

With Supabase configured, create the tables by running `backend/supabase_schema.sql` in the SQL Editor. The API then reads and writes through the Data API.

With the two Supabase variables empty, the API uses a local SQLite file and `init-db` creates its tables:

```bash
cd backend
py -3 -m pip install -r requirements.txt
copy .env.example .env
py -3 manage.py init-db
```

`init-db` runs `db.create_all()` for that local file. On Windows, `py -3` is the Python launcher. Use `python3` if that is what your machine provides. Python 3.11 or newer is expected.

### Seed data

The seed script creates the Campus Bites Canteen workspace and does not run as part of application startup.

```bash
cd backend
py -3 seed.py
```

`py -3 seed.py --reset` deletes that demo workspace and rebuilds it. Other businesses are left alone.

| Account | Password | Role |
| --- | --- | --- |
| `admin@stockwise.demo` | `Admin@12345` | Owner |
| `staff@stockwise.demo` | `Staff@12345` | Staff |

Join code: `CAMPUS`. The seed includes 20 products, 5 suppliers, 30 days of sales and consumption, low-stock and critical examples, and sample purchase orders.

## Run the application

Terminal 1, API:

```bash
cd backend
py -3 run.py
```

Terminal 2, UI:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. With Supabase configured, register a workspace. Otherwise sign in with the demo owner from the seed script.

## API

All routes are under `/api`. Protected routes expect `Authorization: Bearer <token>`. Errors use `{ "error": "...", "details": ... }` and standard HTTP status codes. Lists use `{ "data": [...] }`. A missing record in another business returns 404.

| Method | Path | Access |
| --- | --- | --- |
| POST | `/api/auth/register` | Public |
| POST | `/api/auth/login` | Public |
| POST | `/api/auth/logout` | Signed in |
| GET | `/api/auth/me` | Signed in |
| POST | `/api/auth/forgot-password` | Public |
| POST | `/api/auth/reset-password` | Public |
| PUT | `/api/auth/password` | Signed in |
| GET, PUT | `/api/business` | Read: any member. Write: owner |
| POST | `/api/business/setup` | Owner, before setup is complete |
| GET, POST | `/api/categories` | Read: any. Write: owner |
| DELETE | `/api/categories/:id` | Owner |
| GET, POST | `/api/users` | Owner |
| PUT | `/api/users/:id` | Owner |
| GET, POST | `/api/products` | Read: any. Create: owner |
| GET, PUT, DELETE | `/api/products/:id` | Read: any. Write: owner |
| GET, POST | `/api/transactions` | Any member |
| GET | `/api/transactions/summary` | Any member |
| POST | `/api/transactions/import` | Any member |
| GET | `/api/transactions/template` | Any member |
| GET, POST | `/api/suppliers` | Read: any. Create: owner |
| GET | `/api/suppliers/compare` | Any member |
| GET, PUT, DELETE | `/api/suppliers/:id` | Read: any. Write: owner |
| POST | `/api/suppliers/:id/products` | Owner |
| GET | `/api/restock/recommendations` | Any member |
| POST | `/api/restock/optimize` | Any member |
| GET, POST | `/api/restock/purchase-orders` | Any member. `approve: true` requires owner |
| PUT | `/api/restock/purchase-orders/:id` | Owner |
| POST | `/api/restock/purchase-orders/:id/receive` | Any member |
| GET | `/api/analytics/overview` | Any member |
| GET | `/api/analytics/demand` | Any member |
| GET | `/api/analytics/forecasts` | Any member |
| GET | `/api/notifications` | Any member |
| GET | `/api/search` | Any member |
| POST | `/api/simulations/demand-spike` | Any member |
| POST | `/api/simulations/supplier-delay` | Any member |
| POST | `/api/simulations/budget-reduction` | Any member |
| GET | `/api/simulations/history` | Any member |
| POST | `/api/simulations/:id/rerun` | Any member |
| GET | `/api/reports/<name>` | Any member. `format=csv` downloads a file |
| GET | `/api/health` | Public |

Report names: `inventory`, `low-stock`, `critical`, `sales`, `wastage`, `suppliers`, `restocking`, `purchase-orders`, `forecast`.

## Tests

```bash
cd backend
py -3 -m pytest -q
```

Tests cover the hash map, heap, queue, sliding window, both sorts, the rice urgency example, greedy versus knapsack divergence, login and isolation, stock rejection, partial delivery, staff restrictions, and a demand-spike simulation that leaves stock unchanged. The test configuration uses an in-memory SQLite database so PostgreSQL does not have to be running.

## Deployment

### One Vercel project (frontend and API together)

The repository root of this folder (`StockWise/`) holds `vercel.json`, `api/index.py` and a slim `requirements.txt`. Vercel builds the React app and runs the Flask API as a Python function on the same site, under `/api`. Nothing else needs hosting: the data lives in Supabase.

1. Import the repository in Vercel. Set **Root Directory** to `StockWise` (the folder that contains this README) and **Application Preset** to **Other**. Do not pick "Services" or "Import single project".
2. Add environment variables:
   - `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` (used by the API)
   - `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` (the same two values, used by the browser)
   - `FLASK_ENV=production`, `RETURN_RESET_TOKEN=0`, and a long random `SECRET_KEY`
   - Leave `VITE_API_URL` unset. Production builds call `/api` on the same site, so CORS is not involved
3. Deploy. Check `https://YOUR-APP.vercel.app/api/health` returns `{"status": "ok"}`.
4. In Supabase → Authentication → URL configuration, set the Site URL to your Vercel URL and add `https://YOUR-APP.vercel.app/reset-password` to the redirect allow list.
5. Run `backend/supabase_schema.sql` once in the Supabase SQL Editor if you have not already.

Supabase is required for this setup. Vercel functions have no writable disk, so the local SQLite mode only works on your own machine.

### Alternative: API on Render

`render.yaml` deploys only the API to Render (set `rootDir` to the backend folder's path in your repository). Then deploy `frontend` on Vercel with `VITE_API_URL` pointing at the Render URL, and set `CORS_ORIGINS` and `FRONTEND_URL` on Render to the Vercel URL. Render does not read the `Procfile`; the start command is in `render.yaml`.

## Known limits

- Forecasts are statistical estimates. They are not a guarantee of next week's sales.
- The knapsack uses whole currency units. Paisa-level costs are rounded to the nearest rupee for the table.
- Each user belongs to one workspace. There is no workspace switcher.
- Password reset emails send only when SMTP is configured. In development the API can return the reset URL instead.
- PDF export is not included. Reports download as CSV, and the browser can print the on-screen table.
- SQLite is for tests and for a machine that has not set the Supabase URL and publishable key. With those two values set, data goes through the Supabase Data API.
