import { useLayoutEffect, useRef, useState } from "react";

/** Serializes form submissions, including events in the same render. */
export function usePendingAction() {
  const [pending, setPending] = useState(false);
  const current = useRef<{ cancelled: boolean } | null>(null);

  useLayoutEffect(
    () => () => {
      if (current.current) current.current.cancelled = true;
    },
    []
  );

  async function run(action: (isCurrent: () => boolean) => Promise<void>) {
    if (current.current) return;
    const submission = { cancelled: false };
    current.current = submission;
    setPending(true);
    try {
      await action(() => !submission.cancelled);
    } finally {
      current.current = null;
      if (!submission.cancelled) setPending(false);
    }
  }

  return { pending, run };
}
