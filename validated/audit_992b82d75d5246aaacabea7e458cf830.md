### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the value passed by the pool — which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. If the router is allowlisted (a natural admin action to enable router-mediated swaps), every unprivileged user can bypass the curated pool's allowlist by routing through the router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool.swap() calls _beforeSwap(msg.sender, ...)  // sender = router
     → ExtensionCalling._beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → checks allowedSwapper[pool][router]  ← wrong actor
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap()` with no mechanism to forward the original user's identity: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural action to let allowlisted users trade via the standard periphery) inadvertently opens the pool to **all users**. Any address can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the curated pool; the extension sees `sender = router`, which is allowlisted, and the swap proceeds. The allowlist is completely ineffective for router-mediated swaps. This constitutes unauthorized trading on a pool designed to restrict access, with direct fund-impact consequences (e.g., a pool designed for KYC'd users or a specific counterparty set is now open to the public, exposing LPs to toxic flow the allowlist was meant to prevent).

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a routine and expected action for any curated pool that intends to support the standard periphery. The `generate_scanned_questions.py` audit target explicitly identifies this path: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* No privileged attacker capability is needed beyond calling the public router. [5](#0-4) 

---

### Recommendation

The pool's `swap()` interface does not carry an explicit `originator` field. Two remediation paths exist:

1. **Extension-side**: Require the `extensionData` payload to carry the original user's address (signed or verified), and have `SwapAllowlistExtension` decode and check that address instead of `sender`.
2. **Router-side**: Have the router encode the original `msg.sender` into `extensionData` for each hop, and have the extension decode and verify it. The extension should reject calls where `sender` is a known router but `extensionData` does not carry a valid user identity.

Either way, the allowlist must gate the economically relevant actor (the user whose funds are being spent), not the intermediary contract.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists the router (standard practice)
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker: not allowlisted
address attacker = makeAddr("attacker");
// attacker is NOT in allowedSwapper[pool]

// Attacker routes through the router
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    recipient: attacker,
    deadline: block.timestamp,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    extensionData: ""
}));
// Swap succeeds: extension checked allowedSwapper[pool][router] == true
// Attacker bypassed the allowlist
```

The pool's `_beforeSwap` receives `sender = router`, the extension checks `allowedSwapper[pool][router]` (true), and the swap executes for the non-allowlisted attacker. [6](#0-5) [7](#0-6)

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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
