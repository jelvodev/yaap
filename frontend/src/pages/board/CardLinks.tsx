import { Link } from "react-router-dom";

import type { CardDetail } from "./boardApi";

/** What the card is tied to; only the links that exist. Every id is a number, nothing here is built from card text. */
export default function CardLinks({ links }: { links: CardDetail["links"] }) {
  const { conversations, plan_id, review_id, branch, pull_request } = links;
  const empty = conversations.length === 0 && !plan_id && !review_id && !branch && !pull_request;
  if (empty) return null;
  return (
    <section className="card-detail-section">
      <h3>Linked</h3>
      <div className="card-detail-box card-detail-links">
        {conversations.map((conversation) => (
          <div key={conversation.id}>
            <Link to={`/chat/${conversation.id}`}>{conversation.title}</Link>{" "}
            <span className="faint small">{conversation.role}</span>
          </div>
        ))}
        {plan_id ? <div>Plan #{plan_id}</div> : null}
        {review_id ? <div>Review #{review_id}</div> : null}
        {branch ? <div className="mono">{branch}</div> : null}
        {pull_request ? <div>Pull request #{pull_request}</div> : null}
      </div>
    </section>
  );
}
