### Title
`SwapAllowlistExtension` checks the router address instead of the actual end-user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument it receives from the pool. Because `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender`, and `MetricOmmSimpleRouter` is the immediate caller when users route through it, the extension checks whether the **router** is allowlisted rather than the actual end-user. A pool admin who allowlists the router to support standard periphery swaps inadvertently opens the pool to every user, defeating the per-user curation the extension was designed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, ...))
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly — making the router the `msg.sender` of the pool call:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`. The actual end-user's address is never consulted.

**Contrast with `DepositAllowlistExtension`:** that extension correctly ignores `sender` and checks `owner` — the economically relevant actor explicitly passed by the caller — so the deposit path does not share this flaw. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of counterparties. Because the extension checks the router address instead of the end-user:

- **If the router is allowlisted** (required for any router-mediated swap to succeed): every user on the network can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The curation is completely nullified.
- **If the router is not allowlisted**: all router-mediated swaps revert for every user, including legitimately allowlisted ones, breaking the standard periphery path.

In the first scenario — the operationally realistic one — any disallowed user can trade against a curated pool's liquidity, causing direct loss of LP assets through unwanted price exposure and fee leakage to unauthorized counterparties.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the protocol's standard swap periphery. Pool admins who deploy a curated pool and want it to be usable via the router must allowlist the router address. This is the expected operational path, making the bypass reachable by any user with no special privileges, no malicious setup, and no non-standard tokens. The attacker simply calls `exactInputSingle` on the router targeting the curated pool.

---

### Recommendation

Gate on the **end-user** rather than the immediate pool caller. Two approaches:

1. **Check `recipient` instead of `sender`** — `recipient` is the address that receives output tokens and is the economically relevant actor for a swap. The pool already passes it as the second argument to `beforeSwap`.

2. **Require the router to forward the original `msg.sender`** — add a `swapperOverride` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and check that value when present.

The simplest fix consistent with the existing interface is option 1:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
    so that normal router-mediated swaps work.
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not in allowlist) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: attacker, ...})
  2. Router calls pool.swap(attacker, ...) — msg.sender of pool.swap = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true (router is allowlisted).
  5. Swap executes. Attacker receives output tokens.

Expected: revert NotAllowedToSwap (attacker is not in allowlist).
Actual:   swap succeeds — allowlist bypassed.
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
