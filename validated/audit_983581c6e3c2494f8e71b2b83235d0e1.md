### Title
SwapAllowlistExtension gates the router address instead of the actual user when swaps are routed through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. A pool admin who allowlists the router to let their users access the standard periphery inadvertently opens the pool to every user, completely bypassing the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` at the pool: [4](#0-3) 

The actual user's address (`msg.sender` of the router call) is stored only in the transient callback context for payment purposes and is never forwarded to the pool or to any extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for any pool admin who configures a curated pool:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard periphery |
| Allowlist the router | **Every** user can bypass the per-user allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The `DepositAllowlistExtension` does not share this flaw because the pool explicitly passes the `owner` address (the economically relevant actor) as a separate parameter, and the liquidity adder preserves that identity: [5](#0-4) 

For swaps there is no equivalent "owner" field — the router never surfaces the real initiator to the pool or its extensions.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural action to give their users access to the standard interface) grants every unprivileged user the ability to swap on that pool. The allowlist — the sole access-control boundary for that pool — is silently voided. Any user can drain LP-owned token inventory at oracle-quoted prices that were intended only for vetted counterparties, constituting a direct admin-boundary break with fund-impacting consequences for LPs.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the production periphery swap interface. A pool admin who wants their allowlisted users to have a normal UX will allowlist the router. The bypass requires no special knowledge: any user simply calls `exactInputSingle` on the router pointing at the curated pool. No privileged access, no malicious setup, and no non-standard token behavior is required.

---

### Recommendation

The extension must gate on the economically relevant actor, not the immediate caller. Two viable approaches:

1. **Caller-forwarded identity**: Define a convention where the router encodes the real user's address in `extensionData` (e.g., as the first 20 bytes), and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router. This requires the router to cooperate and the extension to trust the encoding.

2. **Recipient-as-gated-actor**: If the pool's swap semantics guarantee that `recipient` is always the economic beneficiary, gate on `recipient` instead of `sender`. This is simpler but changes the semantics of the allowlist (it gates who receives output, not who initiates).

The cleanest long-term fix mirrors how `DepositAllowlistExtension` works: the pool should expose a dedicated "initiator" field in the swap hook arguments that the router populates with `msg.sender` before calling the pool, so extensions always see the real user regardless of routing depth.

---

### Proof of Concept

```
Setup:
  pool = curated MetricOmmPool with SwapAllowlistExtension
  admin allowlists: userA, router (to give userA router access)
  userB is NOT allowlisted

Attack:
  userB calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient=userB, ...)
  → pool passes msg.sender=router as sender to _beforeSwap
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  → swap executes for userB despite userB not being on the allowlist

Result:
  userB swaps on a pool that was supposed to be restricted to userA only.
  The per-user allowlist is completely bypassed.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
