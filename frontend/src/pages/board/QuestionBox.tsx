import { useState } from "react";

import { Markdown } from "../../components/message/Markdown";
import type { CardDetail } from "./boardApi";

interface Props {
  questions: CardDetail["questions"];
  busy: boolean;
  onAnswer: (index: number, text: string) => Promise<boolean>;
}

function Question({
  index,
  question,
  busy,
  onAnswer,
}: {
  index: number;
  question: CardDetail["questions"][number];
  busy: boolean;
  onAnswer: Props["onAnswer"];
}) {
  const [text, setText] = useState("");
  const answered = Boolean(question.answer);
  async function submit(): Promise<void> {
    if (await onAnswer(index, text.trim())) setText("");
  }
  return (
    <div className={answered ? "card-detail-box" : "card-detail-box card-detail-warning"}>
      <Markdown text={question.text} plain />
      {question.asked && <div className="faint small">Asked {question.asked}</div>}
      {answered ? (
        <p className="card-detail-answer">{question.answer}</p>
      ) : (
        <>
          <textarea
            rows={3}
            placeholder="Your answer…"
            aria-label="Your answer"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <div className="card-detail-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={busy || text.trim() === ""}
              onClick={() => void submit()}
            >
              Answer
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default function QuestionBox({ questions, busy, onAnswer }: Props) {
  return (
    <section className="card-detail-section">
      <h3>Questions</h3>
      {questions.map((question, index) => (
        <Question key={index} index={index} question={question} busy={busy} onAnswer={onAnswer} />
      ))}
    </section>
  );
}
