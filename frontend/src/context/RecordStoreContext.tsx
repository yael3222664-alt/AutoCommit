import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";

import { api } from "../services/api";
import { createRecordStore, type RecordStore, type RecordStoreState } from "../store/recordStore";

const RecordStoreContext = createContext<RecordStore | null>(null);

interface RecordStoreProviderProps {
  children: ReactNode;
  /** Inject a store (e.g. with a mocked API client); defaults to one backed by the real API. */
  store?: RecordStore;
}

export function RecordStoreProvider({ children, store }: RecordStoreProviderProps) {
  const [ownStore, setOwnStore] = useState<RecordStore | null>(null);

  // The store is created and disposed by the same effect run, so a remount (e.g. StrictMode's
  // mount → unmount → mount in development) gets a fresh store instead of a disposed one.
  useEffect(() => {
    if (store) return undefined;
    const created = createRecordStore(api);
    setOwnStore(created);
    return () => {
      created.dispose();
      setOwnStore((current) => (current === created ? null : current));
    };
  }, [store]);

  const value = store ?? ownStore;
  if (!value) return null;
  return <RecordStoreContext.Provider value={value}>{children}</RecordStoreContext.Provider>;
}

export function useRecordStore(): RecordStore {
  const store = useContext(RecordStoreContext);
  if (!store) throw new Error("useRecordStore must be used inside <RecordStoreProvider>");
  return store;
}

export function useRecordState<T>(selector: (state: RecordStoreState) => T): T {
  const store = useRecordStore();
  return useSyncExternalStore(store.subscribe, () => selector(store.getState()));
}
