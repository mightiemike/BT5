The call chain is fully traceable and the vulnerability is real. Here is the complete analysis:

---

### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Swap Allowlist via Allowlisted Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the router address is allowlisted, any arbitrary EOA can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

**Exact call chain:**

1. Arbitrary EOA calls `MetricOmmSimpleRouter.exactInputSingle()`.
2. Router calls `pool.swap(recipient, ...)` — so `msg.sender` inside `MetricOmmPool.swap()` is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — passing the **router** as `sender`. [1](#0-0) 

4. `ExtensionCalling._beforeSwap` forwards `sender=router` verbatim to the extension. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. [3](#0-2) 

If the pool admin has allowlisted the router address (a realistic configuration — e.g., to restrict swaps to the official router, or to allow the router to facilitate swaps on behalf of users), the check passes for **every user** who calls through the router, regardless of whether that user is individually allowlisted.

The extension's stated purpose is to gate `swap` by swapper address per pool: [4](#0-3) 

But the `sender` it receives is the immediate caller of `pool.swap()`, not the originating EOA. There is no mechanism in the extension or the pool to recover the true end user.

---

### Impact Explanation

The swap allowlist is completely bypassed. Any non-allowlisted EOA can execute swaps on a pool that is supposed to be restricted to specific addresses, simply by routing through `MetricOmmSimpleRouter`. This breaks the core access-control invariant of the extension and allows unauthorized users to trade against pool liquidity — directly affecting pool operators who deployed the allowlist to restrict participation (e.g., KYC-gated pools, private pools, or pools restricted to specific counterparties).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public swap interface. Pool admins who want to restrict swaps to specific users while still allowing router-mediated execution would naturally allowlist the router address. The `setAllowedToSwap` admin function accepts any address, and there is no documentation warning against allowlisting router contracts. The bypass requires no special privileges — any EOA with tokens can exploit it. [5](#0-4) 

---

### Recommendation

The extension must check the **originating user**, not the immediate `pool.swap()` caller. Two options:

1. **Pass `tx.origin` as an additional parameter** in the extension hook (not recommended — `tx.origin` is unsafe for contract wallets and AA).
2. **Require direct pool interaction**: document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps, and revert if `sender` is a known router/contract (e.g., check `sender == tx.origin`).
3. **Preferred**: Redesign the allowlist to check both `sender` and an additional `originator` field passed through `extensionData`, letting the router forward the true caller explicitly and verifiably (e.g., signed by the router).

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass_viaRouter() public {
    // Setup: pool with SwapAllowlistExtension, only router is allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // arbitraryEOA is NOT individually allowlisted

    // arbitraryEOA calls router — router calls pool.swap(sender=router)
    // extension checks allowedSwapper[pool][router] == true → passes
    vm.prank(arbitraryEOA);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        ...
    }));
    // Swap succeeds — allowlist bypassed
}
```

The extension check at line 37 evaluates `allowedSwapper[pool][router]` (true), never inspecting `arbitraryEOA`. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
