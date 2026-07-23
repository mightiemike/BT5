### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the direct caller of `pool.swap()`. When swaps are mediated through `MetricOmmSimpleRouter`, `sender` is the router, not the end-user. To allow router-mediated swaps at all, the pool admin must allowlist the router address. Doing so grants every user unrestricted swap access through the router, completely defeating the allowlist guard.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is implemented as:

```solidity
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
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the address that called `pool.swap()`. The check is therefore `allowedSwapper[pool][caller_of_swap]`.

`ExtensionCalling._beforeSwap` passes `msg.sender` of the `swap()` call as `sender`:

```solidity
function _beforeSwap(
    address sender,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(
            IMetricOmmExtensions.beforeSwap,
            (sender, ...)
        )
    );
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput*()`, the router calls `pool.swap(recipient, ...)`. At that point `msg.sender` inside the pool is the **router**, so `sender` forwarded to `beforeSwap` is the **router address**, not the end-user.

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps always revert — router is unusable |
| **Allowlist the router** | Every user can bypass the allowlist by routing through the router |

There is no configuration that simultaneously allows router-mediated swaps and restricts which end-users may swap. The allowlist guard is structurally misbound to the wrong identity for the router path.

This is the direct analog of the external report's `protocolFeeAddress` bug: in both cases a configured guard/address operates on the wrong value — there, fees are minted to `address(0)` because the recipient is never initialized; here, the allowlist is evaluated against the router address because the hook receives the wrong identity, making the guard permanently bypassable for any user who routes through the public router.

---

### Impact Explanation

**High.** Any user can swap on a pool that the admin intended to restrict to a specific allowlist by simply routing through `MetricOmmSimpleRouter`. The allowlist extension provides zero protection for router-mediated swaps once the router is allowlisted. Pools that rely on the allowlist to gate access to a private or compliance-restricted market are fully open to the public via the router. Unauthorized swaps against an oracle-priced pool can drain LP inventory at oracle-fair prices, causing direct loss of LP principal if the pool was designed to serve only trusted counterparties.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented in the protocol. Any pool admin who deploys a `SwapAllowlistExtension` and also wants users to be able to use the standard router will naturally allowlist the router address. The bypass requires no exploit setup beyond using the public router — it is the default user path. [3](#0-2) 

---

### Recommendation

The `beforeSwap` hook receives both `sender` (direct caller) and `recipient`. Neither is reliably the end-user in a router-mediated flow. Two options:

1. **Check `recipient` instead of `sender`** — the router passes the end-user as `recipient`. This is only correct if the pool enforces that `recipient == end-user`, which the router does for single-hop swaps.

2. **Pass the end-user through `extensionData`** — require the router to encode the originating user in `extensionData` and have the extension decode and check it. The pool admin must then trust the router to populate this field honestly.

3. **Document that the allowlist gates the direct caller only** and that pools using `SwapAllowlistExtension` must not allowlist any intermediary contract (including the router) if per-user gating is required.

Option 1 is the simplest fix for the router path:

```solidity
function beforeSwap(address sender, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    address subject = (sender != address(0) && isKnownRouter[sender]) ? recipient : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][subject]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (extension1) wired to beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(bob_as_recipient, ...)
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes successfully despite not being on the allowlist

Result:
  - Bob bypasses the allowlist entirely.
  - Any user can repeat this, making the SwapAllowlistExtension ineffective for all router-mediated swaps.
``` [1](#0-0) [2](#0-1) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
