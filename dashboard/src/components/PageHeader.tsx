import type { ReactNode } from "react";

export function PageHeader({
  title,
  children,
  actions,
  eyebrow,
}: Readonly<{
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}>) {
  return (
    <div className="page-header">
      <div>
        {eyebrow && <p className="mono muted eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        <p className="lead">{children}</p>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}
