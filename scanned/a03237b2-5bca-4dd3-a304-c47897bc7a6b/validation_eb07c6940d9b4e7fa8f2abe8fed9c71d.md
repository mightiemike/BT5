### Title
Swap Allowlist Checks Router Address Instead of Actual User, Enabling Full Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. The allowlist therefore checks the router's address, not the individual user's address. A pool admin cannot simultaneously allow allowlisted users to swap through the router and block non-allowlisted users from doing the same, because the router address is the only identity the guard ever sees.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter`, `sender` = router address, not the end user.

The allowlist lookup therefore resolves to `allowedSwapper[pool][router]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → all router-mediated swaps revert, even for allowlisted users.
- **Allowlist the router** → every address on the network can bypass the per-user gate by routing through `MetricOmmSimpleRouter`.

The `DepositAllowlistExtension` has a parallel but distinct issue: it ignores the `sender` argument entirely (the first `address` parameter is unnamed/discarded) and checks only `owner`: [3](#0-2) 

Any non-allowlisted caller can pass an allowlisted address as `owner` to `pool.addLiquidity`, causing the deposit guard to pass while the actual payer is unapproved. [4](#0-3) 

---

### Impact Explanation

**Swap allowlist bypass (Critical/High):** Once the router is allowlisted (the only way to let legitimate users swap through it), every unprivileged address can call `MetricOmmSimpleRouter` and trade in a pool that was explicitly restricted. The pool admin's intent — restrict swaps to a curated set of counterparties — is completely nullified. Unauthorized traders can execute swaps, draining pool liquidity at oracle-quoted prices and extracting value from LPs who deposited under the assumption that only approved parties could trade.

**Deposit allowlist bypass (Medium):** A non-allowlisted address can call `pool.addLiquidity(allowlistedAddress, salt, deltas, ...)`, pass the guard, and add liquidity to restricted bins. The LP shares go to the allowlisted `owner`, but the non-allowlisted caller controls which bins receive liquidity and at what share amounts, allowing unauthorized manipulation of pool bin state and pricing.

---

### Likelihood Explanation

The swap bypass is trivially reachable: `MetricOmmSimpleRouter` is a public, permissionless contract. Any user who knows the pool has a swap allowlist can call the router directly. No special privileges, flash loans, or multi-step setup are required. The deposit bypass requires only knowing an allowlisted address (observable on-chain from past `AllowedToDepositSet` events).

---

### Recommendation

**Short term:**

1. `SwapAllowlistExtension.beforeSwap` should check the **original caller** rather than the pool-level `sender`. One approach: require the router to forward the originating user address in `extensionData`, and have the extension decode and check that address. Alternatively, the extension can reject the router address itself and require direct pool calls only.

2. `DepositAllowlistExtension.beforeAddLiquidity` should check `sender` (the first, currently discarded argument) **in addition to** `owner`, or replace the `owner` check with a `sender` check, depending on whether the intent is to gate the payer or the LP beneficiary.

**Long term:** Redesign the extension interface so that the original end-user identity is always propagated through the periphery call stack (e.g., via a trusted forwarder pattern or explicit `originalCaller` field), making it impossible for intermediate contracts to silently substitute their own address as the gated identity.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension in beforeSwap order
  pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack:
  bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(pool, zeroForOne, amount, ...)
  router calls pool.swap(recipient=bob, ...)
  pool calls _beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] == true  → passes
  bob's swap executes at oracle price, draining pool reserves

Result:
  bob, who was never allowlisted, successfully swaps in a restricted pool.
  The allowlist guard is completely bypassed.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
