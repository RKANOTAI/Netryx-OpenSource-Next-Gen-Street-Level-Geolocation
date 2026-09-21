export function createPreviewRegistry(urlApi = globalThis.URL) {
  const urls = new Set();
  const createObjectURL = urlApi?.createObjectURL?.bind(urlApi);
  const revokeObjectURL = urlApi?.revokeObjectURL?.bind(urlApi);

  return {
    add(file) {
      if (!createObjectURL || !file) return null;
      const url = createObjectURL(file);
      urls.add(url);
      return url;
    },
    remove(url) {
      if (!url || !urls.has(url)) return;
      urls.delete(url);
      revokeObjectURL?.(url);
    },
    cleanup() {
      for (const url of urls) revokeObjectURL?.(url);
      urls.clear();
    },
  };
}
