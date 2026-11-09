---
name: react-state
description: Decide where state lives in a React app, keeping server data, URL state, form state and UI state apart.
  Use it whenever you add state, reach for a global store or context, copy fetched data into state, write an effect
  that sets state, or fix a screen that shows stale data.
license: MIT
metadata:
  sources: wshobson/plugins/frontend-mobile-development/skills/react-state-management
---

# React state

Every value has one owner. Most state bugs are a value kept in two places that drift apart, or a value stored that
should have been computed. Before adding state, decide which kind it is.

## 1. Name the kind of state

| Kind | Examples | Where it lives |
|---|---|---|
| Server data | orders, the current user, search results | a fetching library's cache (TanStack Query, SWR, the router's loaders) |
| URL state | filters, the open tab, the page, a selected id | the URL's path and search parameters |
| Form state | field values, touched, errors | the form (a form library or local state in the form) |
| UI state | open menu, hover, an expanded row | `useState` in the nearest component that needs it |
| Shared client state | the theme, a draft across pages, an undo stack | context, or a small store when many parts read it |
| Derived values | totals, filtered lists, "is valid" | nowhere: compute them while rendering |

Pick the first row that fits. Global client state is the last resort, not the default.

## 2. Compute instead of storing

A value that follows from props or other state is computed during render. Storing it needs an effect to keep it in
sync, and that effect runs one render late.

```tsx
// Not this: a second copy that must be kept in sync.
const [visible, setVisible] = useState<Order[]>([]);
useEffect(() => setVisible(orders.filter((order) => order.status === status)), [orders, status]);

// This: one source, computed when needed.
const visible = orders.filter((order) => order.status === status);
```

Wrap it in `useMemo` only when the profiler shows the computation is slow.

## 3. Server data stays in the cache

Do not copy fetched data into `useState` or a store: the copy goes stale and every mutation has to update both. Let
the fetching library own it, and invalidate after a change.

```tsx
const orders = useQuery({
  queryKey: ["orders", status],
  queryFn: () => api.listOrders(status),
});

const queryClient = useQueryClient();
const cancel = useMutation({
  mutationFn: api.cancelOrder,
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ["orders"] }),
});
```

Render the three states every request has: pending, error and data. An empty list is a fourth worth designing.

## 4. Put shareable state in the URL

If a user would expect a reload, the back button or a shared link to keep it (a filter, a tab, a page number), it
belongs in the URL. Read it from the router's search parameters and write it back there; do not mirror it in
`useState`.

## 5. Keep local state local

- Start state in the component that uses it. Lift it to the closest common parent only when a sibling needs it.
- Related values that change together go into one `useReducer` with named actions, so no update leaves them half
  changed.
- To reset a component's state when its subject changes, give it a `key` (`<OrderForm key={order.id} />`) instead of
  an effect that clears fields.
- Never mutate state; build a new object or array.

## 6. Effects are for the outside world

`useEffect` synchronises with something outside React: a subscription, a timer, a browser API, a non-React widget. It
is not for computing values or for reacting to an event. Code that responds to a click belongs in the click handler.
Every effect that subscribes returns a cleanup.

## 7. Context and stores

Context suits values that change rarely and many components read (theme, locale, the signed-in user). Every consumer
re-renders when the value changes, so split a busy context in two or move fast-changing values to a small store with
selectors. Pick one store library per app and use it the same way everywhere.

## 8. One hook per concern

When a component holds state and effects for more than one concern, move each concern into its own hook
(`useOrderFilters`, `useOrderStream`). The component then composes and stays readable, and each hook can be tested on
its own.

## Report

When you changed where state lives, say it in this shape:

```
State: <the value>
Kind: <server, URL, form, UI, shared, derived>
Owner: <where it lives now, and what was removed>
Checked: <the test or the screen state you looked at>
```
