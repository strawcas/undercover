import type { ReactNode } from "react";

export function PageHeader({
  title,
  children,
  actions,
}: Readonly<{ title: string; children: ReactNode; actions?: ReactNode }>) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        <p className="lead">{children}</p>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}
