### Title
`SwapAllowlistExtension` gates the router address instead of the end-user identity, allowing any unprivileged user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. A pool admin who allowlists the router so that legitimate users can access the pool through the standard interface inadvertently opens the gate to every unprivileged caller, because the extension has no visibility into the original `msg.sender` of the router call.

---

### Finding Description

**Call chain:**

```
Attacker EOA
  → MetricOmmSimpleRouter.exactInputSingle(...)
      → IMetricOmmPoolActions(pool).swap(recipient, ...)
          [msg.sender inside pool = router address]
          → MetricOmmPool._beforeSwap(msg.sender=router, ...)
              → ExtensionCalling._callExtensionsInOrder(...)
                  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                      checks: allowedSwapper[pool][router]  ← router address, not attacker
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` (the router) as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` faithfully forwards that value: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`msg.sender` here is the pool; `sender` is the router. The extension has no access to the original EOA that called the router.

The router stores the real payer only in transient callback context for payment settlement — it is never forwarded to the pool's `swap` call or to any extension: [4](#0-3) 

**The inescapable dilemma for the pool admin:**

| Admin choice | Effect |
|---|---|
| Allowlist the router | Every EOA on the network can swap — allowlist is nullified |
| Do not allowlist the router | Allowlisted users cannot use the standard interface at all |

There is no configuration that simultaneously allows allowlisted users to use `MetricOmmSimpleRouter` and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or a closed beta) loses that restriction entirely the moment the admin allowlists the router for legitimate users. Any unprivileged attacker can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router and execute swaps against the pool. Depending on pool depth and oracle pricing, this enables:

- Unauthorized extraction of token0/token1 from the pool, directly reducing LP principal.
- Circumvention of any regulatory or contractual access control the pool admin intended to enforce.
- Broken core swap functionality: the guard that is supposed to fail closed instead fails open for the entire public.

---

### Likelihood Explanation

The scenario requires the admin to allowlist the router — a natural and expected step when deploying a pool that is meant to be accessible through the protocol's standard periphery. The `FullMetricExtension` integration test confirms the intended pattern: the test allowlists a `TestCaller` wrapper (the direct pool caller) rather than the router, which works only because the test bypasses the router entirely. [5](#0-4) 

Any real deployment that wants allowlisted users to use the router will hit this path. The `generate_scanned_questions.py` audit target explicitly flags this exact concern: [6](#0-5) 

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economically relevant actor — the EOA initiating the trade — not the intermediate contract. Two viable fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (requires a trust assumption that only the allowlisted router can forge this field, enforced by `onlyPool` on the extension side).

2. **Pool-side**: Add a separate `originator` field to the `swap` signature that the pool passes through to extensions, allowing the router to supply the real EOA. Extensions can then gate on `originator` instead of `sender`.

Either approach must ensure the router cannot be used to spoof an arbitrary allowlisted address.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only userA is allowlisted.
// Admin also allowlists the router so userA can use the standard interface.
swapExtension.setAllowedToSwap(address(pool), userA, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // ← necessary for userA to use router

// Attacker (not allowlisted) calls the router directly.
vm.prank(attacker); // attacker != userA, not in allowlist
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds: extension checked allowedSwapper[pool][router] == true,
// never inspecting `attacker`. Allowlist is bypassed.
```

The pool's `_beforeSwap` receives `sender = address(router)`: [1](#0-0) 

The extension approves it because `allowedSwapper[pool][router] == true`: [7](#0-6)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
