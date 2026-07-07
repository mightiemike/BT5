### Title
Silent Token Transfer Success on Destructed Contract Enables Collateral Loss Without Token Movement — (`core/contracts/libraries/ERC20Helper.sol`)

---

### Summary

`ERC20Helper.safeTransfer` and `safeTransferFrom` use low-level `.call()` without verifying that code exists at the token address. The EVM returns `(success=true, data="")` for calls to addresses with no deployed code. The library's success check — `success && (data.length == 0 || abi.decode(data, (bool)))` — silently passes in this case, causing the protocol to believe a token transfer succeeded when no tokens were moved. This is the direct Nado analog of the Gnosis contract-existence omission.

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
``` [1](#0-0) 

When `address(self)` has no deployed code (e.g., the token contract was self-destructed), the EVM call returns `success = true` and `data = ""`. The guard evaluates as:

```
true && (0 == 0 || ...) → true
```

The `require` does not revert. The caller receives no indication that the transfer failed.

The same flaw exists in `safeTransferFrom`: [2](#0-1) 

`ERC20Helper` is imported and used via `using ERC20Helper for IERC20Base` in both `Clearinghouse` and `EndpointStorage`: [3](#0-2) 

The critical call site is `Clearinghouse.handleWithdrawTransfer`, which is the internal function executed for every user collateral withdrawal and insurance withdrawal:

```solidity
function handleWithdrawTransfer(
    IERC20Base token,
    address to,
    uint128 amount,
    uint64 idx
) internal virtual {
    token.safeTransfer(withdrawPool, uint256(amount));
    BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
}
``` [4](#0-3) 

`handleWithdrawTransfer` is invoked from `withdrawCollateral` and `withdrawInsurance`: [5](#0-4) 

---

### Impact Explanation

If the token contract registered for a product is non-existent at call time, `token.safeTransfer(withdrawPool, amount)` silently returns without reverting. The protocol then:

1. Has already decremented the user's spot balance in `SpotEngine` (the balance update precedes the transfer call in `withdrawCollateral`).
2. Calls `submitWithdrawal`, recording the withdrawal as completed.

The user's on-chain collateral balance is permanently zeroed in the protocol's accounting, but no tokens are ever transferred. The user suffers a total loss of the withdrawn amount with no revert, no error event, and no recovery path. The corrupted state delta is: `SpotEngine` balance for the user's subaccount is decremented by `amount`, while the actual ERC-20 token balance of `withdrawPool` is unchanged. [6](#0-5) 

---

### Likelihood Explanation

**Low.** Requires a token registered in the protocol's `SpotEngine` to have no deployed code at its stored address. This can occur if: (a) the token contract contains a `selfdestruct` path and it is triggered; (b) the token undergoes a migration where the old contract is destroyed; or (c) on chains that support contract redeployment (CREATE2), the token is destroyed and a different contract is deployed at the same address. While major stablecoins are unlikely to be destructed, the protocol supports arbitrary ERC-20 tokens added by the deployer, and the absence of a guard makes any such token a silent failure vector.

---

### Recommendation

**Short Term:** Add an `extcodesize` guard in `ERC20Helper.safeTransfer` and `safeTransferFrom` before the low-level call:

```solidity
uint256 codeSize;
assembly { codeSize := extcodesize(self) }
require(codeSize > 0, "ERC20Helper: token has no code");
```

**Long Term:** Replace the hand-rolled `ERC20Helper` with OpenZeppelin's `SafeERC20`, which includes a contract-existence check via `Address.functionCall` internally. Ensure all token addresses stored in `SpotEngine` are validated for code existence at registration time and re-validated before any transfer.

---

### Proof of Concept

1. Token contract `T` is registered in `SpotEngine` for product ID `P` via `addOrUpdateProduct`.
2. `T` is self-destructed (e.g., via a `selfdestruct` opcode in the token implementation).
3. User submits a `WithdrawCollateral` signed transaction for product `P`, amount `A`.
4. Sequencer processes it: `EndpointTx.processTransactionImpl` → `clearinghouse.withdrawCollateral(sender, P, A, sendTo, nSubmissions)`.
5. Inside `withdrawCollateral`, `spotEngine.updateBalance(P, sender, -amountRealized)` decrements the user's balance.
6. `handleWithdrawTransfer(token, sendTo, A, idx)` is called.
7. `token.safeTransfer(withdrawPool, A)` executes `address(T).call(...)`. Since `T` has no code, EVM returns `(true, "")`.
8. `require(true && (true || ...))` passes — no revert.
9. `submitWithdrawal` records the withdrawal as pending.
10. User's subaccount balance is now `0` in `SpotEngine`. No tokens were transferred. Funds are permanently lost. [7](#0-6) [4](#0-3)

### Citations

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
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

**File:** core/contracts/Clearinghouse.sol (L22-23)
```text
    using MathSD21x18 for int128;
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/Clearinghouse.sol (L269-292)
```text
    function withdrawInsurance(bytes calldata transaction, uint64 idx)
        external
        virtual
        onlyEndpoint
    {
        IEndpoint.WithdrawInsurance memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.WithdrawInsurance)
        );
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int256 multiplier = int256(
            10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
        );
        int128 amount = int128(txn.amount) * int128(multiplier);
        require(amount <= insurance, ERR_NO_INSURANCE);
        insurance -= amount;

        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(
            spotEngine.getConfig(QUOTE_PRODUCT_ID).token
        );
        require(address(token) != address(0));
        handleWithdrawTransfer(token, txn.sendTo, txn.amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-430)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
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

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }

    function _validateNlpRebalance(
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18,
        int128 deltaQuoteAmount
    ) internal pure {
        require(
            nlpPools.length == nlpPoolRebalanceX18.length,
            ERR_INVALID_NLP_REBALANCE
```
