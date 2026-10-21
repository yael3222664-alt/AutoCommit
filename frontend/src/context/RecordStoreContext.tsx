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
  const [ownStore] = useState(() => store ?? createRecordStore(api));
  const value = store ?? ownStore;

  useEffect(() => {
    if (store) return undefined;
    return () => ownStore.dispose();
  }, [store, ownStore]);

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
