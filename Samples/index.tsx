import React, { useState } from "react";

interface Props { title: string; initial?: number }

export default function Counter({ title, initial = 0 }: Props) {
  const [n, setN] = useState<number>(initial);
  return (
    <button onClick={() => setN(n + 1)}>
      {`${title}: ${n}`}
    </button>
  );
}
