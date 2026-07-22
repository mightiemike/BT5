### Title
SwapAllowlistExtension Checks Router Address Instead of End-User Identity, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router address is allowlisted (a natural admin action to support router-mediated swaps), every user — including non-allowlisted ones — can bypass the per-pool swap allowlist by routing through the public router.

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `CallExtension.callExtension`), and `sender` is the argument the pool forwarded. The pool's `_beforeSwap` dispatcher passes whatever address called `pool.swap()` as `sender`:

```solidity
// ExtensionCalling.sol lines 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
  )
);
```

When a user calls `MetricOmmSimpleRouter.exactInput(...)` (or any `exact*` entry point), the router calls `pool.swap(recipient, ...)` on behalf of the user. At that point `msg.sender` inside the pool is the **router contract**, so `sender` forwarded to the extension is the router's address, not the end user's address.

The extension therefore checks `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (a natural step when the pool is meant to be accessible via the periphery), the check passes for **every caller of the router**, regardless of whether the actual end user is on the allowlist.

The identity mismatch is structural: the allowlist is keyed on the direct caller of `pool.swap()`, but the economically relevant actor — the one initiating and benefiting from the swap — is the user who called the router.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled bots). Once the pool admin allowlists the router to support periphery access, the allowlist is effectively nullified: any address can call `MetricOmmSimpleRouter` and execute swaps on the restricted pool. This constitutes a complete bypass of the swap access-control invariant, allowing unauthorized parties to trade against pool liquidity, extract value through oracle-priced swaps, and interact with pools that were designed to be closed to the public.

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router address — a routine and expected configuration step for any pool that intends to support router-mediated swaps. No privileged escalation, malicious setup, or non-standard token behavior is needed. Any unprivileged user can then call the public router to bypass the allowlist. The bypass is reachable on every chain where the router is deployed alongside a `SwapAllowlistExtension`-gated pool.

### Recommendation

The `SwapAllowlistExtension` should gate the **end user** rather than the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the true initiator through the extension data**: The router should encode the original `msg.sender` in `extensionData` and the extension should decode and check that value instead of (or in addition to) `sender`.

2. **Alternatively, check `sender` only when it is not a known intermediary**: The extension can maintain a registry of trusted forwarders and, when `sender` is a forwarder, require the extension data to carry a signed or encoded end-user identity.

The `DepositAllowlistExtension` does not share this exact flaw because it gates `owner` (the position recipient), which is explicitly supplied by the caller and represents the economically relevant party for deposits.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order)
  admin allowlists router: swapExtension.setAllowedToSwap(pool, address(router), true)
  alice (non-allowlisted) wants to swap

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(alice_recipient, ...)
  pool calls _beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] → true → passes
  swap executes for alice despite alice not being on the allowlist

Result:
  alice successfully swaps on a pool that should have blocked her.
  The swap allowlist invariant is broken.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
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
