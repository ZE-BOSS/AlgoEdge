/** Downsample to at most n points, keeping the first and last. */
export function thin(arr, n = 300) {
  if (!arr || arr.length <= n) return arr || [];
  const step = (arr.length - 1) / (n - 1);
  const out = [];
  for (let i = 0; i < n; i += 1) out.push(arr[Math.round(i * step)]);
  return out;
}

