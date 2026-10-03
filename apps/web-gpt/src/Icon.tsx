const paths: Record<string, string> = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  network:
    "M6 6l12 0M6 6l6 12M18 6l-6 12 M3 3h6v6H3z M15 3h6v6h-6z M9 15h6v6H9z",
  compare: "M4 4h6v16H4z M14 4h6v16h-6z M7 8v8 M17 8v8",
  cube: "M12 3l9 5v9l-9 5-9-5V8z M3 8l9 5 9-5 M12 13v9 M7 5l9 5",
  file: "M14 2H5v20h14V7z M14 2v5h5 M8 12h8 M8 16h6",
  arrow: "M4 12h16 M14 6l6 6-6 6",
  chevron: "M9 5l7 7-7 7",
  down: "M6 9l6 6 6-6",
  download: "M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5",
  check: "M5 12l4 4L19 6",
  close: "M6 6l12 12 M18 6L6 18",
  warning: "M12 3L2 21h20z M12 9v5 M12 17v1",
  clock: "M12 8v4l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  play: "M7 4l14 8-14 8z",
  pause: "M7 4v16 M17 4v16",
  refresh: "M20 7a9 9 0 1 0 1 8 M20 2v6h-6",
  expand: "M3 9V3h6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6",
  help: "M9 8a3 3 0 1 1 5 2c-2 1-2 2-2 3 M12 17v1 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  layers: "M2 8l10-5 10 5-10 5z M2 12l10 5 10-5 M2 16l10 5 10-5",
  search: "M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  target:
    "M12 2v4 M12 18v4 M2 12h4 M18 12h4 M19 12a7 7 0 1 1-14 0 7 7 0 0 1 14 0",
  plus: "M12 5v14 M5 12h14",
  minus: "M5 12h14",
  water: "M12 2C9 7 4 11 4 15a8 8 0 0 0 16 0c0-4-5-8-8-13 M8 16c2 3 5 3 8 0",
  pulse: "M2 12h5l3-8 4 16 3-8h5",
  shield: "M12 2l9 4v6c0 6-9 10-9 10S3 18 3 12V6z M8 12l3 3 5-6",
  link: "M9 15l6-6 M8 16l-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0 M16 8l2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0",
};
export default function Icon({
  name,
  size = 20,
  className = "",
}: {
  name: string;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] ?? paths.grid} />
    </svg>
  );
}
