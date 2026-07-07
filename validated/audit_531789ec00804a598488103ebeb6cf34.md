### Title
Silent ERC20 Low-Level Call Succeeds on Codeless Token Address, Enabling Free Balance Credit on Deposit — (`core/contracts/libraries/ERC20Helper.sol`)

---

### Summary

`ERC20Helper.safeTransfer` and `ERC20Helper.safeTransferFrom` perform low-level `.call()` on a token address without verifying that the address contains deployed code. Per the EVM specification, a `.call()` to an address with no code returns `(true, "")`. The success check in both helpers passes silently, meaning no tokens are actually moved. This is consumed by `handleDepositTransfer` in `EndpointStorage`, which only guards against `address(0)` — not against a non-zero address with no code. An attacker who deposits against a product whose token has self-destructed (or was never deployed) receives a credited internal balance without transferring any real tokens.

---

### Finding Description

`ERC20Helper.safeTransfer` and `safeTransferFrom` are implemented as:

```solidity
(bool success, bytes memory data) = address(self).call(
    abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
);
require(
    success && (data.length == 0 || abi.decode(data, (bool))),
    ERR_TRANSFER_FAILED
);
``` [1](#0-0) [2](#0-1) 

When `address(self)` has no deployed code, the EVM returns `success = true` and `data = ""`. The condition `success && (data.length == 0 || ...)` evaluates to `true && true`, so the `require` passes and the function returns without reverting — and without transferring any tokens.

`handleDepositTransfer` in `EndpointStorage` calls both helpers back-to-back:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
}
``` [3](#0-2) 

The only guard is `address(token) != address(0)`. A non-zero address with no code passes this check. Both `safeTransferFrom` and `safeTransferTo` silently succeed, no tokens move, and the deposit transaction is queued in `slowModeTxs` for sequencer processing. [4](#0-3) 

When the sequencer processes the queued `DepositCollateral` transaction, `Clearinghouse.depositCollateral` credits the user's internal spot balance unconditionally: [5](#0-4) 

The same silent-success pattern also affects the withdrawal path. `Clearinghouse.withdrawCollateral` checks `address(token) != address(0)` but not code existence before calling `handleWithdrawTransfer` → `token.safeTransfer(withdrawPool, amount)`. If the token has no code, the transfer silently succeeds, the user's internal balance is decremented, but no tokens are sent to the withdraw pool — the user loses their balance with no payout. [6](#0-5) 

`BaseWithdrawPool.getToken` also only checks `address(token) != address(0)`: [7](#0-6) 

---

### Impact Explanation

**Deposit path (attacker gains):** If a registered product's token address has no code (e.g., the token contract self-destructed), an attacker calls `depositCollateralWithReferral` with that `productId`. Both `safeTransferFrom` and `safeTransferTo` silently succeed. The sequencer processes the queued transaction and credits the attacker's internal balance. The attacker now holds a real internal balance backed by zero real tokens. They can use this balance to trade against other users or withdraw other collateral assets, draining the protocol's real token reserves.

**Withdrawal path (victim loses):** A legitimate user withdrawing a product whose token has no code has their internal balance decremented while receiving nothing. Their funds are permanently lost within the protocol.

---

### Likelihood Explanation

The trigger requires a registered product token to be at a non-zero address with no deployed code. This can occur if:
- A token contract calls `selfdestruct` (present in some upgradeable proxy tokens or tokens with emergency shutdown mechanisms).
- On chains that support EIP-6780 post-Cancun, `selfdestruct` within the same transaction still zeroes code.
- A product is misconfigured with a pre-computed CREATE2 address before the token is deployed (the address is non-zero but codeless at deposit time).

The scenario is not theoretical: several DeFi tokens have included `selfdestruct` paths. Once triggered, the vulnerability is immediately exploitable by any user who calls `depositCollateralWithReferral` with the affected `productId`.

---

### Recommendation

Add a code-existence check in `ERC20Helper` before the low-level call, or centralize the check in `handleDepositTransfer` and `withdrawCollateral`:

```solidity
require(address(self).code.length > 0, "ERC20Helper: token has no code");
```

Alternatively, use OpenZeppelin's `Address.functionCall` which includes this check internally. The guard `address(token) != address(0)` in `handleDepositTransfer`, `withdrawCollateral`, and `BaseWithdrawPool.getToken` must be extended to also verify `address(token).code.length > 0`.

---

### Proof of Concept

1. Token contract `T` is deployed at non-zero address `0xABCD...` and registered as the token for `productId = 5` in the SpotEngine.
2. `T` calls `selfdestruct`; `0xABCD...` now has no code but is non-zero.
3. Attacker calls `Endpoint.depositCollateralWithReferral(subaccount, 5, 1_000_000, "")`.
4. Inside `handleDepositTransfer`: `require(address(token) != address(0))` passes (`0xABCD != 0`).
5. `safeTransferFrom(token, attacker, 1_000_000)` → `.call()` to codeless `0xABCD` → returns `(true, "")` → `require(true && true)` passes. No tokens pulled from attacker.
6. `safeTransferTo(token, clearinghouse, 1_000_000)` → same → passes. No tokens sent to clearinghouse.
7. `slowModeTxs` records a `DepositCollateral{sender: subaccount, productId: 5, amount: 1_000_000}`.
8. Sequencer processes the transaction; `Clearinghouse.depositCollateral` credits attacker's subaccount with `amountRealized` units of product 5.
9. Attacker now holds a credited balance backed by zero real tokens and can withdraw other collateral or trade against solvent subaccounts.

### Citations

**File:** core/contracts/libraries/ERC20Helper.sol (L14-20)
```text
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
```

**File:** core/contracts/libraries/ERC20Helper.sol (L29-41)
```text
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
```

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Endpoint.sol (L144-166)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L400-413)
```text
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);
```

**File:** core/contracts/BaseWithdrawPool.sol (L200-204)
```text
    function getToken(uint32 productId) internal view returns (IERC20Base) {
        IERC20Base token = IERC20Base(spotEngine().getConfig(productId).token);
        require(address(token) != address(0));
        return token;
    }
```
