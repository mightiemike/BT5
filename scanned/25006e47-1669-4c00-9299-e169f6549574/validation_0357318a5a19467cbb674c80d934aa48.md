### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` Due to Wrong-Actor Binding — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is `msg.sender` from the pool's perspective — the **direct caller of `pool.swap()`**. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. The extension therefore checks whether the **router** is allowlisted, not the individual user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

**Hook binding (pool → extension):**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // direct caller of pool.swap() — the router when routed
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
    sender,   // = router address, not the originating user
    ...
))
``` [2](#0-1) 

**Extension check:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the router:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

**Router entry point:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` to the pool:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The originating user's address is never forwarded to the pool or the extension.

**The inescapable dilemma for the pool admin:**

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — even for individually allowlisted users |
| Router **allowlisted** | Every user bypasses the individual allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified users, institutional participants, or beta testers) provides no effective access control for router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the restricted pool and execute swaps that the allowlist was intended to block. This is a direct admin-boundary break: the pool admin's access control policy is bypassed by an unprivileged path through a standard, publicly accessible periphery contract.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any user aware of the router can trivially route around the allowlist. The pool admin has no mechanism to prevent this without breaking router functionality entirely for all users. Likelihood is **high** for any pool that both uses `SwapAllowlistExtension` and needs to support router-mediated swaps.

---

### Recommendation

The extension must gate the **economically relevant actor** — the originating user — not the intermediary. Two approaches:

1. **Pass the originating user through the router:** Modify `MetricOmmSimpleRouter` to include the originating `msg.sender` in `extensionData`, and modify `SwapAllowlistExtension.beforeSwap` to decode and check that address when present. This requires a trusted convention between the router and the extension.

2. **Check `recipient` instead of `sender` for router flows:** If the pool's design intent is to gate who receives output tokens, check `recipient` in the extension. However, this changes the semantics of the allowlist.

3. **Require direct pool calls for allowlisted pools:** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call the pool directly. This is a usage restriction, not a code fix.

The cleanest fix is option 1: the router should forward the originating user's address in a standardized field of `extensionData`, and the extension should verify it.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// The pool admin must also allowlist the router for router swaps to work.
swapAllowlist.setAllowedToSwap(pool, address(router), true);   // required for router
swapAllowlist.setAllowedToSwap(pool, allowedUser, true);

// Attack: blockedUser (not individually allowlisted) routes through the router.
vm.startPrank(blockedUser);
token0.approve(address(router), type(uint256).max);

// This succeeds because the extension sees msg.sender = router, which IS allowlisted.
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: pool,
    recipient: blockedUser,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    tokenIn: token0,
    extensionData: ""
}));
// blockedUser successfully swapped on a pool they were explicitly excluded from.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
