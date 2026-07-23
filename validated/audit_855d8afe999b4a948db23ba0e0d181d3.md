Audit Report

## Title
Swap Allowlist Bypassed for All EOAs When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives the router contract address as `sender` for every router-mediated swap, not the originating EOA. A pool admin who allowlists the router so that their permitted users can trade through the standard periphery inadvertently opens the pool to every EOA on the network, completely defeating the per-address allowlist.

## Finding Description
The call chain is as follows:

**Step 1 — `multicall` uses `delegatecall`:**
`MetricOmmSimpleRouter.multicall` calls `Address.functionDelegateCall(address(this), data[i])`, so `msg.sender` inside `exactInputSingle` is still the originating EOA. [1](#0-0) 

**Step 2 — `exactInputSingle` stores the EOA as payer, then makes a regular external call to `pool.swap`:**
The EOA is stored in transient storage for the payment callback only. The call to `pool.swap` is a regular external call, so from the pool's perspective `msg.sender` is the router contract. [2](#0-1) 

**Step 3 — `MetricOmmPool.swap` passes `msg.sender` (the router) to `_beforeSwap`:** [3](#0-2) 

**Step 4 — `_beforeSwap` in `ExtensionCalling` forwards `sender` (the router) to the extension:** [4](#0-3) 

**Step 5 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][EOA]`:**
The originating EOA is never visible to the extension; the check always evaluates the router address. [5](#0-4) 

This creates two broken states:
- **Router allowlisted:** every EOA can swap through the router, bypassing the per-address allowlist entirely.
- **Router not allowlisted:** allowlisted EOAs cannot use the router at all; they must call `pool.swap` directly.

The pool admin has no way to simultaneously permit specific EOAs to use the router while blocking others. The EOA identity stored in transient storage is used solely for the payment callback and is never forwarded to the extension hook.

## Impact Explanation
Pool curation via `SwapAllowlistExtension` is broken for all router-mediated swaps. Any non-allowlisted EOA can execute swaps on a supposedly gated pool by routing through `MetricOmmSimpleRouter`, causing unauthorized trades against LP positions. This is broken core functionality with direct fund-flow impact: unauthorized parties trade against LP positions on a pool designed to restrict access.

## Likelihood Explanation
The router is the primary intended swap interface for end users. A pool admin who deploys a curated pool and wants their allowlisted users to use the standard router will naturally allowlist the router address — this is the expected operational pattern. The bypass is trivially reachable by any EOA with no special privileges or setup required.

## Recommendation
The router must forward the originating EOA to the pool so the extension can gate on it. The cleanest fix is to add an explicit `originator` parameter to the `pool.swap` interface, distinct from `msg.sender`, so extensions receive the true initiating address. Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and require EOAs to call `pool.swap` directly for allowlisted pools.

## Proof of Concept

```solidity
// 1. Pool admin sets up allowlist — intends only EOA_A to swap
swapExtension.setAllowedToSwap(address(pool), EOA_A, true);

// 2. Pool admin allowlists the router so EOA_A can use the UI
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 3. Non-allowlisted EOA_B calls router.multicall
vm.prank(EOA_B); // EOA_B is NOT in allowedSwapper
bytes[] memory calls = new bytes[](1);
calls[0] = abi.encodeWithSelector(
    router.exactInputSingle.selector,
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool), ...
    })
);
router.multicall(calls);
// Succeeds: extension checks allowedSwapper[pool][router] = true
// EOA_B's allowlist status is never evaluated

// 4. EOA_B calling pool.swap directly reverts
vm.prank(EOA_B);
pool.swap(...); // reverts NotAllowedToSwap — direct path is correctly gated
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
