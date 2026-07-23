### Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (a natural action to enable router usage), every unprivileged user can bypass the per-user allowlist by routing through the public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (correct), and `sender` is the value the pool passes from its own `msg.sender` — i.e., whoever called `pool.swap()`. [1](#0-0) 

The pool's `swap()` function passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router when routed
    recipient,
    ...
)
``` [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` at the pool is the router contract address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. [3](#0-2) 

The pool admin has two choices:
1. **Do not allowlist the router** → no user can swap through the router on this pool (router usage broken).
2. **Allowlist the router** → every user, including non-allowlisted ones, can bypass the per-user gate by routing through the public `MetricOmmSimpleRouter`.

There is no configuration that allows only allowlisted users to use the router while blocking non-allowlisted users. The allowlist cannot distinguish between "router call from Alice (allowlisted)" and "router call from Charlie (not allowlisted)" because both arrive at the pool with `msg.sender = router`.

### Impact Explanation

A pool admin deploying a curated pool (e.g., KYC-gated, institution-only, or restricted-counterparty) uses `SwapAllowlistExtension` to enforce that only approved addresses can swap. If the admin also allowlists the router to give approved users a better UX, every unprivileged user can bypass the allowlist by calling `MetricOmmSimpleRouter`. Non-approved users can then arbitrage the oracle-anchored pool, extracting value directly from LP positions. This is a direct loss of LP principal — the exact class of impact the allowlist was deployed to prevent.

### Likelihood Explanation

The trigger is a semi-trusted pool admin action: allowlisting the router. This is a natural and expected configuration step for any pool that wants to support the standard periphery UX. The admin has no on-chain signal that doing so opens the pool to all users. The `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it once the router address is allowlisted. [4](#0-3) 

### Recommendation

The `beforeSwap` hook should check the **end user's identity**, not the intermediary's. Two approaches:

1. **Pass the original initiator through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router convention.
2. **Check `recipient` instead of `sender`**: For swap allowlists, the economically relevant actor is the recipient of the output tokens. Gate on `recipient` rather than `sender` so router-mediated swaps are still gated to the intended counterparty.
3. **Document the limitation explicitly**: If the design intent is to gate the direct caller only, document that allowlisting the router opens the pool to all users, so admins can make an informed choice.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists Alice (allowedSwapper[pool][alice] = true)
  - Admin allowlists router (allowedSwapper[pool][router] = true)
    (to let Alice use the router for better UX)

Attack:
  - Charlie (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
  - Router calls pool.swap(recipient=charlie, ...)
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true → PASSES
  - Charlie's swap executes on the curated pool
  - Charlie extracts arbitrage profit from LP positions

Result:
  - The per-user allowlist is fully bypassed
  - LP funds are drained by unauthorized arbitrageurs
  - The allowlist invariant (only approved users can swap) is broken
``` [1](#0-0) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
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
