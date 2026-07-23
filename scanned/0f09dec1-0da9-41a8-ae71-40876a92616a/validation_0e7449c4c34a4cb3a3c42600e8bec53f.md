### Title
SwapAllowlistExtension Bypassed by Router-Mediated Swaps When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the **direct caller of `pool.swap()`** (`sender`), not the ultimate end-user. When `MetricOmmSimpleRouter` is used, `sender` = router address. If a pool admin allowlists the router to let their approved users access router UX (multicall, ETH wrapping, etc.), every unpermissioned user can bypass the per-user allowlist by routing through the same public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the contract calling `beforeSwap`) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call itself.

`MetricOmmPool` calls `ExtensionCalling._beforeSwap(sender, ...)` where `sender` is the direct caller of `swap`: [1](#0-0) [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` — so `msg.sender` to the pool is the **router**, not the end-user:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
``` [3](#0-2) 

The original user's identity (`msg.sender` of the router call) is stored only in transient storage for the payment callback — it is **never forwarded** to the pool or to the extension as `sender`. The `multicall` path (delegatecall) does not change this: the external call to `pool.swap()` still originates from the router contract. [4](#0-3) 

**Bypass path:**

1. Pool admin deploys pool with `SwapAllowlistExtension` as a `beforeSwap` hook, `allowAllSwappers[pool] = false`.
2. Admin allowlists specific trusted addresses: `allowedSwapper[pool][trustedUser] = true`.
3. Admin also sets `allowedSwapper[pool][router] = true` so that trusted users can access router UX (multicall, ETH wrapping, exact-output, multi-hop).
4. Any unpermissioned user calls `router.exactInputSingle(pool, ...)`.
5. Pool calls `extension.beforeSwap(sender=router, ...)` → `allowedSwapper[pool][router] = true` → **check passes**.
6. Unpermissioned user's swap executes on the allowlist-protected pool.

The extension has no mechanism to distinguish "router called by trusted user" from "router called by anyone."

---

### Impact Explanation

The `SwapAllowlistExtension` is the primary on-chain mechanism for pool admins to restrict swap access — e.g., to KYC'd counterparties, whitelisted market makers, or specific protocol integrators. Bypassing it allows arbitrary users to execute swaps on pools that were designed to be access-controlled.

Concrete fund impact: if the allowlist is protecting LPs from toxic order flow (

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
