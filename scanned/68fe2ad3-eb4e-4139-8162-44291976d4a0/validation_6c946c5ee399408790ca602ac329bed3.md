### Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the router is allowlisted for a pool (a necessary step to enable router-mediated swaps), every user — including those explicitly excluded from the allowlist — can bypass the guard by calling the router instead of the pool directly.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (the first parameter) is allowlisted for the calling pool (`msg.sender` inside the extension = the pool): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool's `msg.sender` the router, not the originating user: [4](#0-3) 

The same is true for every hop in `exactInput`: [5](#0-4) 

And for intermediate hops in `exactOutput` (called from inside the callback): [6](#0-5) 

In every router path the extension receives `sender = router_address`. The allowlist lookup `allowedSwapper[pool][router_address]` is therefore the only check that matters. If the pool admin allowlists the router (the only way to permit any router-mediated swap), the guard becomes unconditional for all users: any address can call the router and the extension will pass.

The `DepositAllowlistExtension` has a related but distinct issue: it ignores `sender` entirely and checks only `owner`, which is a caller-supplied argument to `MetricOmmPoolLiquidityAdder.addLiquidityExactShares`. A non-allowlisted caller can pass an allowlisted address as `owner`, causing the extension to approve the deposit while the LP shares are minted to the allowlisted address and tokens are pulled from the non-allowlisted caller. This breaks the deposit gate's invariant (the economically active depositor is not checked) but the direct fund-loss path is weaker than the swap case. [7](#0-6) [8](#0-7) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted strategies) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist guard — the only on-chain enforcement mechanism for this access policy — is bypassed without any privileged action by the attacker. Unauthorized swaps can move the pool cursor, consume LP liquidity at oracle price, and extract value from bins that the pool admin intended to reserve for specific counterparties. This constitutes broken core pool functionality with a direct path to LP asset loss.

---

### Likelihood Explanation

The trigger requires two conditions:

1. A pool is deployed with `SwapAllowlistExtension` in its `BEFORE_SWAP_ORDER`.
2. The pool admin allowlists the router address (or sets `allowAllSwappers = true`, which defeats the allowlist entirely).

Condition 2 is not a malicious or unusual admin action — it is the only way to allow any user to swap through the standard periphery router. A pool admin who configures an allowlist and also wants users to use the router will naturally allowlist the router, unaware that doing so opens the gate to all users. The attacker needs no special role: calling `exactInputSingle` on the router is a standard public action.

---

### Recommendation

The extension must recover the originating user identity rather than relying on the immediate `pool.swap()` caller. Two complementary approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and verifies it. This requires the extension to trust that the pool (and only the pool) forwards `extensionData` unmodified — which the current architecture guarantees since `extensionData` is passed calldata-verbatim.

2. **Check `sender` against a router registry and then read the router's stored payer**: The router already stores the originating payer in transient storage (`_getPayer()`). The extension could call back into the router to retrieve the real payer when `sender` is a known router address.

The simplest safe fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a recognized router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER.
  - Pool admin calls setAllowedToSwap(pool, allowedUser, true).
  - Pool admin calls setAllowedToSwap(pool, router, true)   ← required for any router swap.

Attack:
  1. blockedUser (not in allowedSwapper) calls:
       router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — pool sees msg.sender = router.
  3. _beforeSwap passes sender = router to SwapAllowlistExtension.
  4. Extension checks allowedSwapper[pool][router] → true → passes.
  5. blockedUser's swap executes at oracle price, consuming LP liquidity.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; allowlist is fully bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
