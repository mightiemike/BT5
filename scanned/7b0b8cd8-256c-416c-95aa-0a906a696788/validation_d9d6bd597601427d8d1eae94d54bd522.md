### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the immediate `msg.sender` of `pool.swap()`. When any user routes through `MetricOmmSimpleRouter`, that `sender` is the router contract, not the original user. If the pool admin allowlists the router (which is required for any user to use the router), every unprivileged address can bypass the curated allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So the extension receives `sender = address(router)` and evaluates `allowedSwapper[pool][router]`. The original user's address is never seen by the guard.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken core functionality |
| **Allowlist the router** | Every unprivileged user can bypass the allowlist by routing through the public router |

The second branch is the exploitable path. The router is a public, permissionless contract; any address can call it.

---

### Impact Explanation

**High** — complete bypass of the swap allowlist on any curated pool. A pool intended to restrict trading to KYC'd, whitelisted, or otherwise vetted addresses can be freely traded against by any unprivileged user the moment the pool admin allowlists the router (which is the only way to let legitimate users use the router). The attacker receives real token output from the pool's LP reserves, directly harming LP capital and violating the pool's curation invariant.

---

### Likelihood Explanation

**Medium** — the bypass requires the pool admin to have allowlisted the router address. This is the natural, expected action for any pool that wants its allowlisted users to be able to use the standard periphery. The router is the primary user-facing entry point documented in the periphery layer, so real deployments will routinely reach this state.

---

### Recommendation

The extension must gate the **economically relevant actor** — the original user — not the intermediate router. Two viable approaches:

1. **`extensionData` carries the original user**: Require the router to encode the original `msg.sender` in `extensionData`; the extension decodes and checks that address. The pool admin allowlists individual users, not the router.

2. **Separate router-level allowlist**: The router enforces its own allowlist before calling the pool, and the extension trusts only the router for router-mediated swaps (requires a trusted-router registry).

Either way, `allowedSwapper[pool][router]` must never be the terminal authorization check for individual-user curation.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension wired into beforeSwap.
   allowAllSwappers[pool] = false (default).

2. Pool admin allowlists the router so legitimate users can trade:
   swapExtension.setAllowedToSwap(pool, address(router), true)

3. Attacker (address NOT individually allowlisted) calls:
   router.exactInputSingle({
     pool:      <curated pool>,
     recipient: attacker,
     zeroForOne: true,
     amountIn:  X,
     ...
   })

4. Router executes:
   pool.swap(attacker, true, X, ...) // msg.sender = router

5. Pool calls:
   _beforeSwap(sender=router, ...)

6. SwapAllowlistExtension.beforeSwap checks:
   allowedSwapper[pool][router] → true  ✓ (router was allowlisted in step 2)

7. Swap settles. Attacker receives token output.
   The individual-user allowlist was never consulted.
```

The attacker's address appears nowhere in the allowlist check. The guard passes solely because the router is allowlisted, and the router is a public contract callable by anyone.

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
