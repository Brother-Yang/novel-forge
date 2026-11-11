interface BrandMarkProps {
  compact?: boolean;
  onClick?: () => void;
}

export default function BrandMark({ compact, onClick }: BrandMarkProps) {
  const inner = (
    <>
      <span className="nf-brand__seal" aria-hidden="true">
        墨
      </span>
      <span className="nf-brand__text">
        <span className="nf-brand__en">墨夜书坊</span>
        <span className="nf-brand__zh">长篇小说工坊</span>
      </span>
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        className={`nf-brand${compact ? " nf-brand--compact" : ""}`}
        onClick={onClick}
      >
        {inner}
      </button>
    );
  }

  return <div className={`nf-brand${compact ? " nf-brand--compact" : ""}`}>{inner}</div>;
}
