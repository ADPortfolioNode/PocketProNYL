/**
 * Real-time ChromaDB state manager
 * Tracks collection updates and triggers UI refreshes
 */

let listeners = [];

export const chromaStateManager = {
  /**
   * Subscribe to ChromaDB state changes
   */
  subscribe(callback) {
    listeners.push(callback);
    return () => {
      listeners = listeners.filter(l => l !== callback);
    };
  },

  /**
   * Notify subscribers of collection update
   */
  notifyCollectionUpdate(game, rowsAdded, totalRows) {
    listeners.forEach(callback => {
      callback({ game, rowsAdded, totalRows, timestamp: Date.now() });
    });
  },

  /**
   * Clear all listeners
   */
  clearListeners() {
    listeners = [];
  }
};

export default chromaStateManager;
