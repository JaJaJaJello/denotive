"""Process-wide handles to the open library and the shared image cache."""
store = None   # store.Store
images = None  # media.ImageCache


def open_library(path):
    global store, images
    from .media import ImageCache
    from .store import Store
    if store is not None:
        store.close()
    store = Store(path)
    if images is None:
        images = ImageCache(store)
    else:
        # keep the same cache object so existing signal connections stay valid
        images.store = store
        images._pix.clear()
        images._pending.clear()
        images._missing.clear()
        images.used = 0
    return store
