import "./card-detail.css";

import type { Project } from "../../api/client";
import { Markdown } from "../../components/message/Markdown";
import { useConfirm } from "../../components/dialog/ConfirmDialog";
import type { BoardVersion, CardDetail as CardDetailData } from "./boardApi";
import { trustedOrigin } from "./boardLogic";
import { BrokenPart, Footer, StageBar, StateLine } from "./CardDetailParts";
import { InboxPart, LaterPart } from "./CardColumnPart";
import { TextEditor, TitleEditor } from "./CardEditors";
import CardLinks from "./CardLinks";
import CardModels from "./CardModels";
import QuestionBox from "./QuestionBox";
import { type CardDetailHandle, useCardDetail } from "./useCardDetail";
import { useCardModels } from "./useCardModels";

interface Props {
  projectId: number;
  cardId: number;
  head: string | null;
  project: Project | null;
  versions: BoardVersion[];
  currentVersion: string | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}

function Body({
  card,
  handle,
  props,
  onDrop,
}: {
  card: CardDetailData;
  handle: CardDetailHandle;
  props: Props;
  onDrop: () => void;
}) {
  const preset = useCardModels(props.project);
  const trusted = trustedOrigin(card.origin.type);
  const version = props.versions.find((item) => item.name === card.version) ?? null;
  const column = card.column;
  return (
    <>
      {column === "inbox" && (
        <InboxPart card={card} currentVersion={props.currentVersion} handle={handle} onDrop={onDrop} />
      )}
      {column !== null && column !== "inbox" && column !== "idea" && <LaterPart card={card} handle={handle} />}
      <TextEditor
        heading="Description"
        value={card.description}
        plain={!trusted}
        empty="No description yet."
        busy={handle.busy}
        onSave={(description) => handle.save({ description })}
      />
      {card.questions.length > 0 && (
        <QuestionBox questions={card.questions} busy={handle.busy} onAnswer={handle.answer} />
      )}
      {card.specification.trim() !== "" && (
        <section className="card-detail-section">
          <h3>Specification</h3>
          <Markdown text={card.specification} plain={!trusted} />
        </section>
      )}
      {card.plan_markdown.trim() !== "" && (
        <section className="card-detail-section">
          <h3>Plan</h3>
          <Markdown text={card.plan_markdown} plain />
        </section>
      )}
      {card.evidence.trim() !== "" && (
        <section className="card-detail-section">
          <h3>Evidence</h3>
          <Markdown text={card.evidence} plain />
        </section>
      )}
      <TextEditor
        heading={`Definition of done · ${card.kind ?? "card"}`}
        value={card.definition_of_done}
        plain
        empty=""
        busy={handle.busy}
        onSave={(definition_of_done) => handle.save({ definition_of_done })}
      >
        <ul className="card-detail-checklist">
          {card.definition_items.map((item, index) => (
            <li key={index} className={item.done ? "done" : ""}>
              <span aria-hidden="true">{item.done ? "✓" : "○"}</span> {item.text}
            </li>
          ))}
        </ul>
      </TextEditor>
      <CardLinks links={card.links} />
      <CardModels card={card} preset={preset} version={version} />
      {column !== "inbox" && card.plan?.status !== "building" && (
        <div className="card-detail-actions">
          <button type="button" className="button button-danger" disabled={handle.busy} onClick={onDrop}>
            Drop
          </button>
        </div>
      )}
    </>
  );
}

export default function CardDetail(props: Props) {
  const { projectId, cardId, head, versions, onClose, onChanged } = props;
  const handle = useCardDetail(projectId, cardId, head, onChanged);
  const confirm = useConfirm();
  const { card } = handle;

  async function drop(): Promise<void> {
    const ok = await confirm({
      title: "Drop the card",
      message: `Drop #${cardId}? Its file is removed from the board branch.`,
      confirmLabel: "Drop",
      tone: "danger",
    });
    if (ok && (await handle.drop())) onClose();
  }

  const closeButton = (
    <button type="button" className="button button-ghost" aria-label="Close the card" onClick={onClose}>
      ✕
    </button>
  );
  if (!card) {
    return (
      <div className="card-detail">
        <div className="card-detail-head">
          <span className="faint mono">#{cardId}</span>
          {closeButton}
        </div>
        {handle.loading ? <p className="faint">Loading the card…</p> : <p className="form-error">{handle.error}</p>}
      </div>
    );
  }
  return (
    <div className="card-detail">
      <div className="card-detail-head">
        <span className="faint mono">#{card.id}</span>
        {card.kind && <span className="card-detail-tag">{card.kind}</span>}
        <span className="card-detail-tag">{card.version ?? "not scheduled"}</span>
        {closeButton}
      </div>
      <TitleEditor card={card} versions={versions} handle={handle} />
      <StageBar column={card.column} />
      <StateLine card={card} />
      {handle.error && <div className="form-error">{handle.error}</div>}
      {card.broken ? (
        <>
          <BrokenPart card={card} />
          <div className="card-detail-actions">
            <button type="button" className="button button-danger" disabled={handle.busy} onClick={() => void drop()}>
              Drop
            </button>
          </div>
        </>
      ) : (
        <Body card={card} handle={handle} props={props} onDrop={() => void drop()} />
      )}
      <Footer card={card} />
    </div>
  );
}
