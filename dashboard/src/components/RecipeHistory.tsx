import type { Recipe } from "../api";
import { timestamp } from "../format";
import { Diff } from "./Diff";

export function RecipeHistory({ recipes }: Readonly<{ recipes: Recipe[] }>) {
  return (
    <section className="panel" aria-label="Recipe history">
      <h2>Recipe versions</h2>
      <p className="muted small">
        Approved recipes apply to future rounds. A round keeps the recipe it
        started with.
      </p>
      {recipes.length === 0 ? (
        <p className="message">
          No recipes are available. Contact a senior handler.
        </p>
      ) : (
        <ol className="proposal-history">
          {recipes.map((recipe) => (
            <li key={recipe.version}>
              <details>
                <summary>
                  Recipe version {recipe.version} ·{" "}
                  {recipe.approved_by ?? recipe.proposed_by}
                </summary>
                <p className="muted small">{timestamp(recipe.created_at)}</p>
                <p className="narrative">{recipe.reason}</p>
                <Diff text={recipe.diff} />
                <details>
                  <summary>Complete recipe</summary>
                  <pre className="instructions">{recipe.recipe}</pre>
                </details>
              </details>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
