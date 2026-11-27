export function Message({
  error,
  retry,
}: Readonly<{ error?: Error; retry?: () => void }>) {
  return (
    <div className="message" role={error ? "alert" : "status"}>
      <p>{error ? error.message : "Loading..."}</p>
      {error && retry && (
        <button type="button" className="secondary" onClick={retry}>
          Retry
        </button>
      )}
    </div>
  );
}
