import logoUrl from "../assets/logo.png";

/** Ideal Dynamics logo. In dark mode it sits on a light chip so the navy text stays readable. */
export default function Logo({ className = "" }) {
  return (
    <span className={`logo ${className}`}>
      <img src={logoUrl} alt="Ideal Dynamics - An Integral ERP Suite" />
    </span>
  );
}
