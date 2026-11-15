import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import api, { errorMessage } from "../services/api";
import { useAuth } from "../context/AuthContext";
import PageHeader from "../components/PageHeader";
import ChartCard from "../components/ChartCard";
import DataTable from "../components/DataTable";
import UrgencyBadge from "../components/UrgencyBadge";
import Spinner from "../components/Spinner";
import { inputClass } from "../utils/styles";
import { formatDays, formatMoney, formatQty } from "../utils/format";

export default function Analytics() {
  const { user } = useAuth();
  const [filters, setFilters] = useState({ product_id: "", category: "", from: "", to: "" });
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [demand, setDemand] = useState(null);
  const [forecasts, setForecasts] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load(next = filters) {
    setLoading(true);
    setError("");
    try {
      const params = Object.fromEntries(Object.entries(next).filter(([, value]) => value));
      const [demandResponse, forecastResponse] = await Promise.all([
        api.get("/analytics/demand", { params }),
        api.get("/analytics/forecasts", { params }),
      ]);
      setDemand(demandResponse.data.data);
      setForecasts(forecastResponse.data.data);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    api.get("/products", { params: { per_page: 100 } }).then((response) => setProducts(response.data.data)).catch(() => {});
    api.get("/categories").then((response) => setCategories(response.data.data)).catch(() => {});
    load(filters);
  }, []);

  return (
    <div>
      <PageHeader title="Analytics and forecasts" subtitle="Moving averages describe recent use. They are estimates, and short histories are marked." />
      <div className="mb-4 grid gap-3 md:grid-cols-4">
        <select className={inputClass} aria-label="Product" value={filters.product_id} onChange={(event) => { const next = { ...filters, product_id: event.target.value }; setFilters(next); load(next); }}>
          <option value="">All products</option>
          {products.map((product) => <option key={product.product_id} value={product.product_id}>{product.name}</option>)}
        </select>
        <select className={inputClass} aria-label="Category" value={filters.category} onChange={(event) => { const next = { ...filters, category: event.target.value }; setFilters(next); load(next); }}>
          <option value="">All categories</option>
          {categories.map((category) => <option key={category.id}>{category.name}</option>)}
        </select>
        <input className={inputClass} type="date" aria-label="From" value={filters.from} onChange={(event) => { const next = { ...filters, from: event.target.value }; setFilters(next); load(next); }} />
        <input className={inputClass} type="date" aria-label="To" value={filters.to} onChange={(event) => { const next = { ...filters, to: event.target.value }; setFilters(next); load(next); }} />
      </div>
      {error ? <p className="mb-3 text-sm text-red-700">{error}</p> : null}
      {loading || !demand ? <Spinner label="Loading analytics" /> : (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard title="Consumption value" subtitle="The teal line is daily value. The amber line is the 7-day sliding average.">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={demand.series}>
                  <CartesianGrid stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(value) => formatMoney(value, user.currency)} />
                  <Legend />
                  <Line type="monotone" dataKey="value" name="Daily value" stroke="#0f766e" dot={false} />
                  <Line type="monotone" dataKey="moving_average_7" name="7-day average" stroke="#b45309" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </ChartCard>
            <ChartCard title="Most frequently sold by revenue" subtitle="Ranked with quick sort on sales value.">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={demand.top_products}>
                  <CartesianGrid stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-20} height={60} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(value) => formatMoney(value, user.currency)} />
                  <Bar dataKey="revenue" fill="#0f766e" />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Trend title="Increasing demand" names={demand.increasing} empty="No product is up 15% or more versus the previous week." />
            <Trend title="Declining demand" names={demand.declining} empty="No product is down 15% or more versus the previous week." />
            <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="font-semibold">Inventory value trend</h2>
              <div className="mt-2 h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={demand.inventory_value_trend}>
                    <XAxis dataKey="date" hide />
                    <YAxis hide />
                    <Tooltip formatter={(value) => formatMoney(value, user.currency)} />
                    <Line dataKey="value" stroke="#123152" dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>
          </div>
          <section className="mt-4 rounded-xl border border-slate-200 bg-white shadow-sm">
            <h2 className="px-4 pt-4 font-semibold">Estimated stockout dates</h2>
            <DataTable
              columns={[
                { key: "name", label: "Product" },
                { key: "average_daily_demand", label: "Daily demand", render: (row) => formatQty(row.average_daily_demand, row.unit) },
                { key: "average_7_day", label: "7-day", render: (row) => formatQty(row.average_7_day, row.unit) },
                { key: "average_30_day", label: "30-day", render: (row) => row.average_30_day == null ? "Not enough history" : formatQty(row.average_30_day, row.unit) },
                { key: "days_until_stockout", label: "Stockout", render: (row) => formatDays(row.days_until_stockout) },
                { key: "urgency", label: "Urgency", render: (row) => <UrgencyBadge urgency={row.urgency} /> },
                { key: "trend", label: "Trend" },
                { key: "data_warning", label: "Warning", render: (row) => row.data_warning || "—" },
              ]}
              rows={forecasts}
              rowKey="product_id"
              emptyTitle="No forecast rows"
            />
          </section>
        </>
      )}
    </div>
  );
}

function Trend({ title, names, empty }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="font-semibold">{title}</h2>
      {names?.length ? <ul className="mt-2 space-y-1 text-sm">{names.map((name) => <li key={name}>{name}</li>)}</ul> : <p className="mt-2 text-sm text-slate-500">{empty}</p>}
    </section>
  );
}
