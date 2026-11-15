import { useEffect, useState } from "react";
import api, { errorMessage } from "../services/api";
import PageHeader from "../components/PageHeader";
import DataTable from "../components/DataTable";
import Spinner from "../components/Spinner";
import { btnPrimary, btnSecondary, inputClass } from "../utils/styles";

const REPORTS = [
  ["inventory", "Current inventory"],
  ["low-stock", "Low stock"],
  ["critical", "Critical stock"],
  ["sales", "Sales and consumption"],
  ["wastage", "Wastage"],
  ["suppliers", "Supplier performance"],
  ["restocking", "Restocking expenditure"],
  ["purchase-orders", "Purchase orders"],
  ["forecast", "Forecast and stockout risk"],
];

export default function Reports() {
  const [report, setReport] = useState("inventory");
  const [filters, setFilters] = useState({ from: "", to: "", category: "", product_id: "" });
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get("/products", { params: { per_page: 100 } }).then((response) => setProducts(response.data.data)).catch(() => {});
    api.get("/categories").then((response) => setCategories(response.data.data)).catch(() => {});
  }, []);

  async function load(event) {
    event?.preventDefault();
    setLoading(true);
    setError("");
    try {
      const params = Object.fromEntries(Object.entries(filters).filter(([, value]) => value));
      const response = await api.get(`/reports/${report}`, { params });
      setData(response.data.data);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function download() {
    const params = { ...Object.fromEntries(Object.entries(filters).filter(([, value]) => value)), format: "csv" };
    const response = await api.get(`/reports/${report}`, { params, responseType: "blob" });
    const url = URL.createObjectURL(response.data);
    const link = document.createElement("a");
    link.href = url;
    link.download = `stockwise-${report}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  const columns = (data?.columns || []).map((column) => ({
    key: column.key,
    label: column.label,
    render: (row) => String(row[column.key] ?? "—"),
  }));

  return (
    <div>
      <PageHeader title="Reports" subtitle="Every row is read from the database. CSV downloads use the same filters as the table." />
      <form onSubmit={load} className="mb-4 grid gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-3">
        <select className={inputClass} aria-label="Report" value={report} onChange={(event) => setReport(event.target.value)}>
          {REPORTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <select className={inputClass} aria-label="Category" value={filters.category} onChange={(event) => setFilters({ ...filters, category: event.target.value })}>
          <option value="">All categories</option>
          {categories.map((category) => <option key={category.id}>{category.name}</option>)}
        </select>
        <select className={inputClass} aria-label="Product" value={filters.product_id} onChange={(event) => setFilters({ ...filters, product_id: event.target.value })}>
          <option value="">All products</option>
          {products.map((product) => <option key={product.product_id} value={product.product_id}>{product.name}</option>)}
        </select>
        <input className={inputClass} type="date" aria-label="From" value={filters.from} onChange={(event) => setFilters({ ...filters, from: event.target.value })} />
        <input className={inputClass} type="date" aria-label="To" value={filters.to} onChange={(event) => setFilters({ ...filters, to: event.target.value })} />
        <div className="flex gap-2">
          <button className={btnPrimary}>Show report</button>
          <button type="button" className={btnSecondary} onClick={download} disabled={!data}>Download CSV</button>
        </div>
      </form>
      {error ? <p className="mb-3 text-sm text-red-700">{error}</p> : null}
      {loading ? <Spinner label="Building report" /> : data ? (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
          <h2 className="px-4 pt-4 font-semibold">{data.title}</h2>
          <DataTable columns={columns} rows={data.rows.map((row, index) => ({ ...row, _key: index }))} rowKey="_key" emptyTitle="This report has no rows" />
        </div>
      ) : <p className="text-sm text-slate-500">Choose a report and click Show report.</p>}
    </div>
  );
}
