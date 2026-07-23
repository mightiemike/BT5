### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user's address, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` inside `pool.swap()` is the **router contract**, not the actual user. If the pool admin allowlists the router (necessary for approved users to use the router), every user on-chain can bypass the per-user restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

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

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument the pool passes. The pool always passes its own `msg.sender` as `sender`:

```solidity
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) ... {
    ...
    _beforeSwap(
        msg.sender,   // ← this becomes `sender` in the extension
        recipient,
        ...
    );
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards it unchanged:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router**, not the originating user:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist entry — rather than the actual user's entry. This creates an irresolvable dilemma for pool admins:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | **Blocked** (broken UX) | Blocked |
| Yes | Passes | **Passes** (bypass) |

There is no configuration that simultaneously allows approved users to use the router and blocks unapproved users from using it.

---

### Impact Explanation

Any user can bypass a `SwapAllowlistExtension`-protected pool by routing through `MetricOmmSimpleRouter`. Concrete consequences:

- **Access-control failure**: Pools intended for KYC'd, institutional, or whitelisted counterparties are open to the general public.
- **LP fund loss**: Unrestricted users can execute swaps against oracle-anchored liquidity that was provisioned only for specific counterparties, draining LP positions at prices the LPs did not intend to offer to arbitrary actors.
- **Broken core pool functionality**: The allowlist guard — the primary mechanism for curated pools — silently fails open on the canonical periphery path.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing swap interface; most users will interact through it.
- A pool admin who configures a `SwapAllowlistExtension` and wants approved users to be able to use the router **must** allowlist the router address. This is the natural, expected configuration.
- No special knowledge or privileged access is required; any user can call `router.exactInputSingle` with a valid pool address.
- The bypass is structural and reproducible on every router-mediated swap.

---

### Recommendation

**Short term**: The `SwapAllowlistExtension` should check the **originating user** rather than the direct caller of `pool.swap()`. One approach: the router stores the originating `msg.sender` in transient storage (it already does this for the payer via `_setNextCallbackContext`) and passes it as part of `extensionData` so the extension can read the true initiator. Alternatively, the pool could expose a separate "originator" field in the hook arguments.

**Long term**: Audit all extensions that gate by `sender` and verify that the `sender` binding is semantically correct for every supported periphery entry point (router single-hop, router multi-hop, liquidity adder). Establish a protocol-level convention distinguishing the "economic actor" (the user who initiated the action) from the "direct caller" (the contract that called the pool).

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, zeroForOne, amount, ...)` — pool's `msg.sender` = router.
6. Pool calls `extension.beforeSwap(router, bob, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Swap executes. Bob receives output tokens from a pool he was never authorized to access.

Bob never needed to be on the allowlist. The guard is fully bypassed on the canonical periphery path.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
