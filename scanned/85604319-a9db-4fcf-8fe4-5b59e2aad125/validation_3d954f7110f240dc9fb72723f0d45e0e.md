### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Allowlist via the Public Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, any unauthorized user can bypass the allowlist by routing through the same public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (correct — the pool calls the extension). `sender` is the address the pool forwards as the swap initiator. `ExtensionCalling._beforeSwap` encodes it directly from the pool's own `msg.sender`:

```solidity
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` is called, the router calls `pool.swap(...)` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The pool's `msg.sender` is the **router**, so the extension receives `sender = router`. The extension then checks `allowedSwapper[pool][router]`, not the actual end user.

For a legitimate allowlisted user (e.g., Alice) to use the router, the pool admin **must** also allowlist the router address. Once the router is allowlisted, every user — including unauthorized ones — can bypass the allowlist by calling the router, because the extension cannot distinguish between Alice and Bob when both arrive through the same router address.

The same structural issue exists in the multi-hop `exactInput` path for intermediate hops, where `address(this)` (the router itself) is used as the payer:

```solidity
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
``` [4](#0-3) 

---

### Impact Explanation

The `SwapAllowlistExtension` access control is completely nullified for any pool whose admin allowlists the router. Unauthorized users gain full swap access to pools intended to be restricted to specific counterparties. Depending on the pool's design (e.g., institutional-only pools, pools with specific pricing assumptions about who can trade), this can result in unauthorized extraction of value, price manipulation, or violation of regulatory/contractual access controls — all constituting a direct loss of protocol integrity and potentially of LP assets.

---

### Likelihood Explanation

The trigger is straightforward and requires no special privileges:

1. The pool admin must have allowlisted the router (a necessary step for any legitimate user to use the router with the pool).
2. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool.
3. The router is a public, permissionless contract — no special setup is needed by the attacker.

The condition (router is allowlisted) is a **required operational state** for the allowlist to coexist with router usage, making this reachable in any realistic deployment.

---

### Recommendation

The extension must check the **actual end user**, not the intermediate router. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it, also verifying that the immediate `sender` is a trusted router. This requires a trusted-router registry.

2. **Check `sender` only when `sender` is not a known router**: The extension maintains a registry of trusted routers; when `sender` is a trusted router, it reads the actual user from `extensionData`.

3. **Require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and users must call `pool.swap` directly. This is operationally fragile but avoids code changes.

---

### Proof of Concept

```
1. Admin deploys pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)      // required for alice to use the router
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) with msg.sender = router.
6. Pool calls extension.beforeSwap(router, ...) with sender = router.
7. Extension checks: allowedSwapper[pool][router] == true  → passes.
8. Bob's swap executes successfully despite not being allowlisted.
```

The allowlist is bypassed with zero cost beyond gas. Bob receives the swap output; the pool's intended access restriction is defeated.

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
