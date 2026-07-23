### Title
`SwapAllowlistExtension` checks router address as swapper instead of actual user, enabling allowlist bypass or DoS via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against the per-pool allowlist. The pool sets `sender = msg.sender` at the pool level. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks the router's address, not the real swapper's address, producing two fund-impacting failure modes depending on how the admin configures the allowlist.

---

### Finding Description

**Actor binding in the pool's `swap` function:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol (line 230-240)
_beforeSwap(
  msg.sender,   // ← pool's immediate caller, not the end-user
  recipient,
  zeroForOne,
  amountSpecified,
  priceLimitX64,
  packedSlot0Initial,
  bidPriceX64,
  askPriceX64,
  extensionData
);
``` [1](#0-0) 

**The extension checks that `sender` argument:**

```solidity
// SwapAllowlistExtension.sol (line 31-41)
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)`. At the pool, `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Two concrete failure modes:**

| Scenario | Admin intent | What extension checks | Result |
|---|---|---|---|
| Router NOT allowlisted | Gate specific users | `allowedSwapper[pool][router]` → false | Allowlisted users **cannot swap through the router** (DoS on core path) |
| Router IS allowlisted | Trust the router as an intermediary | `allowedSwapper[pool][router]` → true | **Any user** bypasses the curated pool's access control |

The `DepositAllowlistExtension` does not share this flaw — it correctly checks the `owner` argument (the position owner), not `sender`:

```solidity
// DepositAllowlistExtension.sol (line 38)
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [3](#0-2) 

The asymmetry confirms the swap extension's actor binding is wrong by design comparison.

---

### Impact Explanation

**Bypass path (High):** If the pool admin adds the router to the allowlist (a natural operational step when the router is the intended public entry point), every user — including those explicitly excluded — can trade on the curated pool by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection. This is a direct admin-boundary break: an unprivileged actor bypasses a configured access-control gate.

**DoS path (Medium):** If the router is not allowlisted (the default), every allowlisted user who uses the standard periphery swap path receives `NotAllowedToSwap`. The core swap flow is broken for the intended user population. This is broken core pool functionality causing an unusable swap flow.

Both impacts are within the allowed gate: admin-boundary bypass and broken core swap functionality.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the documented standard periphery entry point for swaps; most users will route through it.
- Pool admins who configure `SwapAllowlistExtension` expect it to gate actual users, not intermediary contracts. The mismatch is non-obvious.
- No special attacker capability is required: any user can call the router.
- The bypass path requires only that the admin adds the router to the allowlist, which is a natural operational action when the router is the intended public interface.

---

### Recommendation

Pass the actual end-user identity through the swap path. Two options:

1. **Add a `swapper` parameter to `swap`**: The pool accepts an explicit `swapper` address (analogous to how `addLiquidity` accepts an explicit `owner`), passes it to `_beforeSwap`, and the extension checks that address. The router forwards `msg.sender` as `swapper`.

2. **Check `recipient` instead of `sender`**: If the protocol's intent is to gate who receives output (not who initiates), check `recipient`. This is a weaker fix because `recipient` can also be set arbitrarily.

Option 1 is the correct structural fix, mirroring the `owner`/`sender` separation already present in the liquidity path.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists alice directly: setAllowedToSwap(pool, alice, true)
  - Admin also allowlists the router: setAllowedToSwap(pool, router, true)
    (natural step: router is the public entry point)

Attack (bypass):
  - bob (NOT allowlisted) calls router.exactInputSingle(pool, ...)
  - router calls pool.swap(recipient=bob, ...)
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] → true
  - swap executes for bob despite bob not being allowlisted

DoS (without router allowlisted):
  - alice (allowlisted) calls router.exactInputSingle(pool, ...)
  - router calls pool.swap(recipient=alice, ...)
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] → false
  - revert NotAllowedToSwap — alice cannot use the standard periphery path
``` [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-40)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
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
