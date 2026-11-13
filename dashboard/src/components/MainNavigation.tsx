import type { ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router";
import {
  claimNavigationSection,
  CONFIGURATION_PAGE,
  IMPROVEMENT_PAGE,
  QUEUE_PAGE,
  SCOREBOARD_PAGE,
  SETTINGS_PAGE,
} from "../paths";

function NavigationItem({
  to,
  parent,
  children,
}: Readonly<{ to: string; parent: string | undefined; children: ReactNode }>) {
  if (parent === to)
    return (
      <Link to={to} className="active" aria-current="page">
        {children}
      </Link>
    );
  return (
    <NavLink to={to} end={to === QUEUE_PAGE}>
      {children}
    </NavLink>
  );
}

export function MainNavigation() {
  const parent = claimNavigationSection(useLocation().pathname);
  return (
    <nav className="nav" aria-label="Main">
      <NavigationItem to={QUEUE_PAGE} parent={parent}>
        Queue
      </NavigationItem>
      <NavigationItem to={CONFIGURATION_PAGE} parent={parent}>
        Configuration
      </NavigationItem>
      <NavigationItem to={IMPROVEMENT_PAGE} parent={parent}>
        Improvement
      </NavigationItem>
      <NavigationItem to={SETTINGS_PAGE} parent={parent}>
        Settings
      </NavigationItem>
      <NavigationItem to={SCOREBOARD_PAGE} parent={parent}>
        Scoreboard
      </NavigationItem>
    </nav>
  );
}
