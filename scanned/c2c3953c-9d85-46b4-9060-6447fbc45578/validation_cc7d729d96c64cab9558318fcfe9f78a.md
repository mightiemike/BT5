### Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is bound to `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router address (the natural step to support router-mediated swaps for legitimate users), every unpermissioned user can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the forwarded first argument: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exact*`, the router calls `pool.swap(...)` on the user's behalf. At that point `msg.sender` inside `pool.swap` is the **router**, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants to support router-mediated swaps for their allowlisted users must add the router to the allowlist. Once the router address is allowlisted, **any** caller — including addresses that were never individually permitted — can reach the pool by routing through `MetricOmmSimpleRouter`, because the extension sees only the allowlisted router address and passes the check unconditionally.

The `DepositAllowlistExtension` does not share this exact flaw because it ignores `sender` and checks `owner` instead: [4](#0-3) 

The swap allowlist has no equivalent fallback; the originating user's address is never consulted.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional, or whitelist-gated) that relies on `SwapAllowlistExtension` to restrict who may trade is fully open to any public user once the router is allowlisted. The attacker can execute swaps at oracle-derived prices, consuming LP liquidity that was intended only for permissioned counterparties. This constitutes a direct loss of LP principal and a broken core pool invariant (access control).

---

### Likelihood Explanation

The trigger is a routine, expected admin action: allowlisting the official `MetricOmmSimpleRouter` so that permitted users can use the standard periphery. Any pool that takes this step is immediately vulnerable. The router is a public, permissionless contract, so no privileged access is required by the attacker. The bypass is reachable on every swap through the router after the admin allowlists it.

---

### Recommendation

The extension must resolve the originating user rather than the immediate caller. Two sound approaches:

1. **Pass the originating user explicitly.** Add a `originSender` field to the extension call data (or use a transient-storage context set by the pool before calling extensions) so the extension can check the true initiator regardless of routing depth.

2. **Gate on recipient instead of sender for router flows.** Alternatively, require the router to forward the user's address in `extensionData` and have the extension decode and verify it, with the pool or router signing/attesting the value.

Either way, the invariant must be: the identity checked by the allowlist is the address that economically benefits from the swap, not the intermediate contract that relayed the call.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to support router-mediated swaps for allowlisted users.
3. Attacker (address never individually allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
4. Router calls `pool.swap(recipient=attacker, ...)`. Inside `pool.swap`, `msg.sender = router`.
5. `_beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → check passes.
7. Attacker's swap executes against LP liquidity at oracle price. The allowlist is fully bypassed. [3](#0-2) [2](#0-1) [5](#0-4)

### Citations

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
