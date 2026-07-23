### Title
SwapAllowlistExtension Bypass via Router: Any User Can Swap on Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates a swap, `sender` becomes the router's address, not the end user's. If the router is allowlisted so that legitimate users can reach the pool through it, the allowlist is silently bypassed for every user — including those the pool admin explicitly excluded.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument the pool passes, which is `msg.sender` of the pool's own `swap()` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
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

When a user calls `MetricOmmSimpleRouter.exactInput()` (or any router entry point), the router calls `pool.swap()` directly. At that point `msg.sender` to the pool is the **router address**, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user, including explicitly blocked ones, can bypass the restriction by routing through the router |
| No | Legitimate allowlisted users cannot use the router at all |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional market makers, KYC'd addresses, or protocol-owned contracts) can be freely traded against by any address that routes through `MetricOmmSimpleRouter`. The allowlist — the sole access-control mechanism for swap gating — is rendered ineffective. Unauthorized traders can extract value from the pool at oracle-derived prices, which the pool admin intended to reserve for specific parties.

### Likelihood Explanation

The router is a public, permissionless periphery contract. Any user can call it. The bypass requires only that the router be allowlisted for the pool (a natural configuration if any legitimate user is expected to use the router). No privileged access, no special setup, and no malicious contract deployment is required — a standard router call suffices.

### Recommendation

The extension must gate the **original end user**, not the intermediary. Two approaches:

1. **Pass the real user via `extensionData`**: Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check it. This requires the extension to trust that the pool's `msg.sender` is a known router (i.e., add a router registry).

2. **Separate router-identity from user-identity at the pool level**: Introduce a dedicated `originSender` field in the swap call that the pool populates from a transient context set by the router before calling `pool.swap()`, and pass that to extensions instead of `msg.sender`.

The simplest safe fix is option 1: the router encodes `abi.encode(msg.sender)` into `extensionData`, and the extension, after verifying `msg.sender` is a trusted router, checks the decoded address against the allowlist.

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension configured.
2. Admin allowlists router R for pool P: allowedSwapper[P][R] = true.
3. Admin explicitly blocks user U: allowedSwapper[P][U] = false (default).
4. U calls MetricOmmSimpleRouter.exactInput(..., pool=P, ...).
5. Router calls P.swap(recipient=U, ..., extensionData="").
6. Pool calls _beforeSwap(msg.sender=R, ...).
7. Extension checks allowedSwapper[P][R] == true → passes.
8. U's swap executes on the restricted pool without restriction.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
