## Tracing the Exact Call Path

**Step 1 — EOA → multicall (delegatecall):**
`MetricOmmSimpleRouter.multicall` uses `Address.functionDelegateCall(address(this), data[i])`. Because this is a `delegatecall`, `msg.sender` inside `exactInputSingle` is still the originating EOA. [1](#0-0) 

**Step 2 — exactInputSingle → pool.swap (regular external call):**
`exactInputSingle` stores the EOA as the payer in transient storage, then makes a regular external call to `pool.swap(...)`. From the pool's perspective, `msg.sender` is now the **router contract**, not the EOA. [2](#0-1) 

**Step 3 — pool.swap → _beforeSwap(msg.sender=router):**
The pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`. [3](#0-2) 

**Step 4 — SwapAllowlistExtension.beforeSwap checks `allowedSwapper[pool][router]`:**
The extension receives `sender = router` and checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router. The originating EOA is never visible to the extension. [4](#0-3) 

---

## Verdict

### Title
Swap Allowlist Bypassed for All EOAs When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives the **router address** as `sender` for every router-mediated swap, not the originating EOA. A pool admin who allowlists the router to permit their users to trade through the official periphery inadvertently opens the pool to **all** EOAs, completely defeating the allowlist.

### Finding Description
When a user calls `MetricOmmSimpleRouter.multicall([exactInputSingle(...)])`:

1. `multicall` uses `delegatecall` to self, so `msg.sender` inside `exactInputSingle` is the EOA.
2. `exactInputSingle` stores the EOA as the payer in transient storage (for callback payment only), then calls `pool.swap(...)` as a regular external call.
3. The pool receives `msg.sender = router` and passes it as `sender` to `_beforeSwap` → `SwapAllowlistExtension.beforeSwap`.
4. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][EOA]`.

The EOA identity is stored in transient storage solely for the payment callback and is never forwarded to the extension hook. There is no mechanism for the extension to recover the originating EOA.

This creates two broken states:
- **If the router is allowlisted:** every EOA on the network can swap through the router, bypassing the allowlist entirely.
- **If the router is not allowlisted:** allowlisted EOAs cannot use the router at all; they must call `pool.swap` directly, which may not be the intended UX.

The pool admin has **no way** to simultaneously allow specific EOAs to use the router while blocking others.

### Impact Explanation
Pool curation via `SwapAllowlistExtension` is broken for all router-mediated swaps. Any non-allowlisted EOA can execute swaps on a supposedly gated pool by routing through `MetricOmmSimpleRouter`, causing unauthorized trades and undermining the pool designer's access control intent. This is broken core functionality with direct fund-flow impact (unauthorized parties trade against LP positions).

### Likelihood Explanation
The router is the primary intended swap interface for end users. A pool admin who deploys a curated pool and wants their allowlisted users to use the standard router will naturally allowlist the router address. This is the expected operational pattern, making the bypass trivially reachable by any EOA.

### Recommendation
The router must forward the originating EOA to the pool so the extension can gate on it. Options:

1. **Pass the originating caller as a parameter to `pool.swap`** (requires a core interface change to add a `caller` field distinct from `msg.sender`).
2. **Have the extension read the payer from the router's transient storage** (tight coupling, fragile).
3. **Require EOAs to call `pool.swap` directly** for allowlisted pools and document that the router is incompatible with `SwapAllowlistExtension` (operational mitigation only).

The cleanest fix is option 1: add an explicit `originator` parameter to the swap interface so extensions can gate on the true initiating address.

### Proof of Concept

```solidity
// 1. Pool admin sets up allowlist — intends only EOA_A to swap
swapExtension.setAllowedToSwap(address(pool), EOA_A, true);

// 2. Pool admin also allowlists the router so EOA_A can use the UI
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
router.multicall(calls); // succeeds — allowlist bypassed

// 4. EOA_B calling pool.swap directly reverts
vm.prank(EOA_B);
pool.swap(...); // reverts NotAllowedToSwap — direct path is gated
```

The extension checks `allowedSwapper[pool][router] = true` in step 3, so the swap succeeds regardless of EOA_B's allowlist status. [5](#0-4) [6](#0-5)

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
