### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end-user, allowing any unprivileged caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap on a given pool. Its `beforeSwap` hook checks the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router (the only way to permit router-mediated swaps), every unprivileged caller gains access regardless of whether they are individually allowlisted, completely defeating the guard.

---

### Finding Description

**Root cause — wrong identity checked in the hook**

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct key for the per-pool mapping). `sender` is the first argument forwarded by the pool. [1](#0-0) 

The pool sets that argument to its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` faithfully forwards it as the first positional argument: [3](#0-2) 

**How the router calls the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

The pool's `msg.sender` is therefore the router contract address. The extension receives `sender = address(router)` and checks `allowedSwapper[pool][router]`.

**The dilemma for the pool admin**

The admin has two mutually exclusive options:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | Every unprivileged user can bypass the allowlist via the router |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

The `SwapAllowlistExtension` is the only on-chain mechanism for a pool admin to restrict swap access to a curated set of addresses (e.g., KYC'd counterparties, protocol-internal actors, or whitelisted market makers). When the guard is bypassed:

- Any unprivileged address can execute swaps on a pool that was intended to be restricted.
- LP assets are exposed to swappers the pool admin explicitly did not authorize.
- The admin-configured access boundary is silently nullified for all router-mediated swap paths (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

This matches the allowed impact gate: **admin-boundary break — factory/oracle role checks are bypassed by an unprivileged path**.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap entry point; virtually all end-user swaps are expected to flow through it.
- The bypass requires no special privilege, no flash loan, and no unusual token behavior — any EOA or contract can call the router.
- The only precondition is that the pool admin has allowlisted the router (which is the natural, expected action to enable normal UX).
- The bypass is therefore reachable by any user on any allowlist-protected pool that supports router swaps.

---

### Recommendation

The extension must gate the **end-user**, not the intermediary. Two complementary fixes:

1. **Check `recipient` as a proxy for the beneficiary** — for single-hop swaps the recipient is the end-user, though it can differ in multi-hop paths.

2. **Preferred: pass the real caller through `extensionData`** — the router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This is the only approach that survives multi-hop routing.

3. **Alternatively, check both `sender` and `recipient`** — require that at least one of them is allowlisted, which covers the common single-hop case without router changes.

Minimal check addition in `SwapAllowlistExtension.beforeSwap`:

```solidity
function beforeSwap(
    address sender,
    address recipient,
    ...
    bytes calldata extensionData
) external view override returns (bytes4) {
    address realCaller = extensionData.length >= 20
        ? abi.decode(extensionData, (address))
        : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][realCaller]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The router must be updated to prepend `abi.encode(msg.sender)` to `extensionData` before forwarding.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension as a beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)   — Alice is the only allowed swapper.
3. Admin calls setAllowedToSwap(pool, router, true)  — router allowlisted so Alice can use it.

Attack
──────
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
       pool:      <restricted pool>,
       recipient: bob,
       ...
   });

5. Router calls pool.swap(bob, zeroForOne, amount, ...).
   pool.msg.sender = router.

6. Pool calls _beforeSwap(router, bob, ...).
   Extension checks: allowedSwapper[pool][router] == true  → passes.

7. Bob's swap executes on the restricted pool.
   The allowlist guard is fully bypassed.

Verification
────────────
If Bob instead calls pool.swap() directly:
   Extension checks: allowedSwapper[pool][bob] == false → reverts NotAllowedToSwap.
The guard only works for direct pool calls, not router-mediated ones.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
