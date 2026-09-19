/*
 * FU-05 (CWE-415, double free) fault-injection interposer for cKDTree.__build.
 *
 * Companion of pyart/map/tests/test_ckdtree_security.py; compiled at test
 * time into a shared object that is LD_PRELOADed into a child interpreter.
 *
 * Design:
 *  - Arms only after the driver performs a magic-size malloc (via ctypes),
 *    so Python interpreter / numpy startup allocations are never perturbed.
 *  - After arming, the first allocation of a size used by cKDTree.__build
 *    (LP64: innernode = 40 B, leafnode = 24 B, mids = m*8 B) marks build
 *    entry; every subsequent such allocation increments a counter, and the
 *    k-th one is failed (returns NULL), emulating an allocation failure
 *    inside the recursive tree build (CERT C ERR33-C / MEM31-C scenario).
 *  - Tracks every pointer returned by ANY interposed allocation routine
 *    (malloc, calloc, realloc, reallocarray, memalign, posix_memalign,
 *    aligned_alloc, valloc, pvalloc) in a hash set, so that free() being
 *    called twice on the same pointer is detected definitively with no
 *    false positives (pointers never seen allocated are ignored).
 *
 * The tracker must stay idempotent for realloc/reallocarray bookkeeping:
 * glibc's reallocarray internally calls the public realloc symbol, so the
 * same pointer gets marked freed twice; only the plain free() path decides
 * whether a second free is a defect.
 *
 * Linux/glibc/LP64 only (uses the __libc_* internal aliases).
 */
#define _GNU_SOURCE
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <errno.h>

extern void *__libc_malloc(size_t size);
extern void *__libc_calloc(size_t nmemb, size_t size);
extern void  __libc_free(void *ptr);
extern void *__libc_realloc(void *ptr, size_t size);
extern void *__libc_memalign(size_t alignment, size_t size);
extern void *__libc_valloc(size_t size);
extern void *__libc_pvalloc(size_t size);
extern void *__libc_reallocarray(void *ptr, size_t n, size_t size);

#define SLOT_EMPTY 0
#define SLOT_LIVE  1
#define SLOT_FREED 2

typedef struct { void *ptr; int state; } slot_t;

static slot_t *tab;
static size_t tab_cap, tab_mask, tab_fill;
static size_t magic_size = 0, fail_at = 0, target_seen = 0;
static int armed = 0, in_build = 0;

static void tab_grow(void) {
    size_t new_cap = tab_cap << 1;
    slot_t *nt = (slot_t *)__libc_calloc(new_cap, sizeof(slot_t));
    if (nt == NULL) return; /* keep old table rather than lose tracking */
    size_t new_mask = new_cap - 1;
    for (size_t i = 0; i < tab_cap; i++) {
        if (tab[i].state != SLOT_EMPTY) {
            size_t j = ((size_t)tab[i].ptr >> 4) & new_mask;
            while (nt[j].state != SLOT_EMPTY) j = (j + 1) & new_mask;
            nt[j] = tab[i];
        }
    }
    tab = nt; tab_cap = new_cap; tab_mask = new_mask;
}

static void tab_init(void) {
    tab_cap = (size_t)1 << 22; /* 4M slots */
    tab = (slot_t *)__libc_calloc(tab_cap, sizeof(slot_t));
    if (tab == NULL) { tab_cap = 0; return; } /* degrade to pass-through */
    tab_mask = tab_cap - 1;
    tab_fill = 0;
    const char *e;
    if ((e = getenv("FU05_MAGIC")) != NULL) magic_size = (size_t)atol(e);
    if ((e = getenv("FU05_FAIL_AT")) != NULL) fail_at = (size_t)atol(e);
}

static void die(const char *msg, void *p) {
    char buf[160];
    int n = snprintf(buf, sizeof buf, "FU05-DOUBLE-FREE-DETECTED %s ptr=%p\n", msg, p);
    if (n > 0) { ssize_t w = write(2, buf, (size_t)n); (void)w; }
    _exit(42);
}

/* Sizes allocated inside cKDTree.__build on LP64: innernode 40, leafnode 24,
 * mids = m * sizeof(float64) (16 for m == 2). */
static int target_size(size_t sz) {
    return sz == 16 || sz == 24 || sz == 40;
}

static void insert_live(void *p) {
    if (p == NULL || tab_cap == 0) return;
    if (tab_fill * 4 >= tab_cap * 3) tab_grow();
    size_t i = ((size_t)p >> 4) & tab_mask;
    while (tab[i].state != SLOT_EMPTY && tab[i].ptr != p) i = (i + 1) & tab_mask;
    if (tab[i].state == SLOT_EMPTY) {
        tab[i].ptr = p; tab[i].state = SLOT_LIVE; tab_fill++;
    } else if (tab[i].state == SLOT_FREED) {
        tab[i].state = SLOT_LIVE;
    }
}

static void mark_freed_if_live(void *p) {
    if (p == NULL || tab_cap == 0) return;
    size_t i = ((size_t)p >> 4) & tab_mask;
    while (tab[i].state != SLOT_EMPTY && tab[i].ptr != p) i = (i + 1) & tab_mask;
    if (tab[i].state == SLOT_LIVE) tab[i].state = SLOT_FREED;
}

void *malloc(size_t size) {
    if (tab == NULL) tab_init();
    if (magic_size != 0 && size == magic_size) {
        armed = 1;
    } else if (armed != 0 && target_size(size)) {
        if (in_build == 0) {
            in_build = 1; /* first target-size allocation marks build entry */
        } else {
            target_seen++;
            if (target_seen == fail_at && fail_at != 0) {
                char buf[128];
                int n = snprintf(buf, sizeof buf,
                    "FU05-INJECTED-FAILURE k=%zu size=%zu\n", target_seen, size);
                if (n > 0) { ssize_t w = write(2, buf, (size_t)n); (void)w; }
                return NULL;
            }
        }
    }
    void *p = __libc_malloc(size);
    insert_live(p);
    return p;
}

void *calloc(size_t n, size_t size) {
    if (tab == NULL) tab_init();
    void *p = __libc_calloc(n, size);
    insert_live(p);
    return p;
}

void *realloc(void *ptr, size_t size) {
    if (tab == NULL) tab_init();
    void *p = __libc_realloc(ptr, size);
    if (tab_cap != 0) {
        if (p == NULL && size != 0) return p; /* failure: old block intact */
        mark_freed_if_live(ptr); /* realloc(p, 0) frees; moved block is gone */
        insert_live(p);
    }
    return p;
}

void *reallocarray(void *ptr, size_t n, size_t size) {
    if (tab == NULL) tab_init();
    void *p = __libc_reallocarray(ptr, n, size);
    if (tab_cap != 0) {
        if (p == NULL && n != 0 && size != 0) return p;
        mark_freed_if_live(ptr);
        insert_live(p);
    }
    return p;
}

void *memalign(size_t alignment, size_t size) {
    if (tab == NULL) tab_init();
    void *p = __libc_memalign(alignment, size);
    insert_live(p);
    return p;
}

int posix_memalign(void **memptr, size_t alignment, size_t size) {
    if (tab == NULL) tab_init();
    if (alignment % sizeof(void *) != 0 ||
        (alignment & (alignment - 1)) != 0)
        return EINVAL;
    void *p = __libc_memalign(alignment, size);
    if (p == NULL) return ENOMEM;
    *memptr = p;
    insert_live(p);
    return 0;
}

void *aligned_alloc(size_t alignment, size_t size) {
    if (tab == NULL) tab_init();
    void *p = __libc_memalign(alignment, size ? size : 1);
    insert_live(p);
    return p;
}

void *valloc(size_t size) {
    if (tab == NULL) tab_init();
    void *p = __libc_valloc(size);
    insert_live(p);
    return p;
}

void *pvalloc(size_t size) {
    if (tab == NULL) tab_init();
    void *p = __libc_pvalloc(size);
    insert_live(p);
    return p;
}

void free(void *ptr) {
    if (tab == NULL) tab_init();
    if (ptr != NULL && tab_cap != 0) {
        size_t i = ((size_t)ptr >> 4) & tab_mask;
        while (tab[i].state != SLOT_EMPTY && tab[i].ptr != ptr) i = (i + 1) & tab_mask;
        if (tab[i].state == SLOT_FREED) {
            die("double-free", ptr);
        } else if (tab[i].state == SLOT_LIVE) {
            tab[i].state = SLOT_FREED;
        }
        /* SLOT_EMPTY: never allocated via any interposed routine
         * (libc-internal strdup, dlopen, etc.) -> ignore. */
    }
    __libc_free(ptr);
}

/* Report how many build-window allocations were observed, so the test can
 * prove the injection window actually covered the tree build (a tree over
 * N points with leafsize L performs thousands of 16/24/40 B allocations). */
__attribute__((destructor)) static void report_stats(void) {
    char buf[128];
    int n = snprintf(buf, sizeof buf,
        "FU05-TARGET-ALLOC-STATS total=%zu armed=%d\n", target_seen, armed);
    if (n > 0) { ssize_t w = write(2, buf, (size_t)n); (void)w; }
}
