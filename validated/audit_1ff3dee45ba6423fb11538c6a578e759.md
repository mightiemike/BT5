### Title
`SwapAllowlistExtension` gates on the direct pool caller (`sender`) rather than the originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap`. When swaps are routed through the public `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating user's. A pool admin who allowlists the router — a necessary step to enable any router-based swap — inadvertently opens the allowlist to every user, including those explicitly excluded.

---

### Finding Description

In `SwapAllowlistExtension.beforeSwap`, the guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by the pool from its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
``` [2](#0-1) 

**Direct swap path:** `sender` = user's EOA → correctly checked against `allowedSwapper[pool][user]`.

**Router swap path:** `MetricOmmSimpleRouter` calls `pool.swap(...)`, so `sender` = router address → the extension checks `allowedSwapper[pool][router]`, not the originating user.

The admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Router swaps blocked for **everyone**, including allowlisted users |
| Allowlist the router | Router swaps open to **everyone**, including non-allowlisted users |

There is no configuration that allows only allowlisted users to swap through the router. The allowlist invariant is structurally broken for the router path.

The `SwapAllowlistExtension` is explicitly listed as a production target whose validation focus is: *"assert the hook cannot be bypassed by routing through an intermediate public contract."* [3](#0-2) 

---

### Impact Explanation

Any user can bypass a per-user swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter` once the admin allowlists the router. This breaks the core access-control guarantee of the extension: unauthorized users can execute swaps on pools intended to be restricted (e.g., KYC-gated, compliance-restricted, or protocol-internal pools). The pool receives tokens and emits swaps as if the allowlist were not present, directly violating the LP-protection invariant the extension is meant to enforce.

---

### Likelihood Explanation

Medium. The trigger is a semi-trusted admin action: allowlisting the router. A pool admin who wants allowlisted users to be able to swap through the standard periphery router will naturally allowlist it, not realizing this opens the gate to all users. `MetricOmmSimpleRouter` is a public, permissionless periphery contract — any address can call it. No further privilege is required after the router is allowlisted.

---

### Recommendation

The extension must check the **originating user**, not the direct pool caller. Two concrete options:

1. **Extension-data forwarding:** Require the router to encode the originating user's address in `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and the extension.

2. **Dual-check pattern:** If `sender` is a known router, decode the real user from `extensionData` and check that address instead.

3. **Documentation gate:** If the design intent is that the allowlist only applies to direct pool calls, document this explicitly and rename the extension to avoid misleading pool admins.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin: setAllowedToSwap(pool, alice, true)          // alice is allowlisted
  admin: setAllowedToSwap(pool, router, true)          // router allowlisted to support alice's router swaps

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInput(pool, ..., extensionData="")

Execution trace:
  router → pool.swap(recipient=bob, ..., extensionData="")
    pool: _beforeSwap(sender=router, ...)
      SwapAllowlistExtension.beforeSwap(sender=router, ...)
        check: allowedSwapper[pool][router] == true  ← passes
    swap executes, bob receives tokens

Result:
  bob bypasses the allowlist; swap settles against pool LP balances
  as if bob were an allowlisted user.
``` [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
