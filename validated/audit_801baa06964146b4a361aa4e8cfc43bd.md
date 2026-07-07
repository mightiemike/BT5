### Title
Slow Mode Fees Permanently Locked in `Endpoint` Contract — (File: `core/contracts/EndpointStorage.sol`, `core/contracts/EndpointTx.sol`)

---

### Summary

Quote tokens collected as slow mode fees are transferred directly into the `Endpoint` contract and tracked in `slowModeFees`, but no function exists anywhere in the contract to withdraw or redistribute these tokens. Unlike `sequencerFee[productId]`, which has a dedicated `DumpFees` claim path, `slowModeFees` accumulates indefinitely with no recovery mechanism, permanently locking user-paid quote tokens in the contract.

---

### Finding Description

When any unprivileged user calls `submitSlowModeTransaction()` with a non-admin transaction type (e.g., `WithdrawCollateral`, `LinkSigner`, `WithdrawCollateralV2`), execution is delegated to `EndpointTx.submitSlowModeTransactionImpl()`. The `else` branch at the bottom of that function fires for all non-owner transaction types:

```solidity
} else {
    chargeSlowModeFee(_getQuote(), sender);
    slowModeFees += SLOW_MODE_FEE;
}
``` [1](#0-0) 

`chargeSlowModeFee` is defined in `EndpointStorage` and performs a real ERC-20 `safeTransferFrom` into `address(this)` — the `Endpoint` proxy itself:

```solidity
function chargeSlowModeFee(IERC20Base token, address from) internal virtual {
    require(address(token) != address(0));
    token.safeTransferFrom(from, address(this), clearinghouse.getSlowModeFee());
}
``` [2](#0-1) 

The `slowModeFees` variable is declared in `EndpointStorage` and is only ever incremented — it is never decremented, reset, or used as a basis for any outbound transfer: [3](#0-2) 

By contrast, the protocol does have a working fee-claim path for `sequencerFee[productId]`: the `DumpFees` slow-mode transaction type zeroes out each product's `sequencerFee` entry and calls `clearinghouse.claimSequencerFees(fees)`: [4](#0-3) 

No equivalent path exists for `slowModeFees`. Searching the entire contract suite, `slowModeFees` is written once (the `+=` above) and read nowhere that results in a token transfer out of the `Endpoint`. The `Endpoint` contract exposes no `withdraw`, `rescue`, or `claimSlowModeFees` function. [5](#0-4) 

---

### Impact Explanation

Every quote token paid as a slow mode fee is transferred to the `Endpoint` contract address and stays there permanently. The protocol team cannot recover these funds; users cannot reclaim them. The locked amount grows monotonically with every slow mode submission. Because the slow mode path is the protocol's censorship-resistance mechanism — the path users take when the sequencer is unresponsive — the volume of locked fees can be significant during any sequencer downtime event, precisely the scenario where users are most likely to use it.

The corrupted state is: `token.balanceOf(address(endpoint))` grows without bound while `slowModeFees` tracks the same amount, but no code path ever reduces either.

---

### Likelihood Explanation

**Medium-High.** The trigger is a standard, permissionless user action: calling `submitSlowModeTransaction` with any of the common transaction types (`WithdrawCollateral`, `WithdrawCollateralV2`, `LinkSigner`, etc.). No special role, no exploit setup, and no sequencer compromise is required. The fee is charged on every such submission. The path is live in production and is the intended fallback for censorship resistance. [6](#0-5) 

---

### Recommendation

Add a privileged `claimSlowModeFees(address recipient)` function to `Endpoint` (callable by `owner()` or the sequencer) that transfers `slowModeFees` worth of quote tokens to `recipient` and resets `slowModeFees` to zero — mirroring the pattern already used for `sequencerFee` via `DumpFees` / `claimSequencerFees`.

---

### Proof of Concept

1. User calls `Endpoint.submitSlowModeTransaction(withdrawCollateralTxBytes)`.
2. `Endpoint` delegatecalls `EndpointTx.submitSlowModeTransactionImpl`.
3. The transaction type is `WithdrawCollateral` — not in the owner-only list — so the `else` branch executes.
4. `chargeSlowModeFee(_getQuote(), msg.sender)` pulls `SLOW_MODE_FEE` quote tokens from the user into `address(Endpoint)`.
5. `slowModeFees += SLOW_MODE_FEE` records the accumulation.
6. The transaction is queued. The tokens sit in `Endpoint` forever.
7. Repeat for every subsequent slow mode submission by any user.
8. No function in `Endpoint`, `EndpointTx`, or any other contract in scope transfers these tokens out or decrements `slowModeFees`.

### Citations

**File:** core/contracts/EndpointTx.sol (L244-253)
```text
        } else if (txType == IEndpoint.TransactionType.DumpFees) {
            IOffchainExchange(offchainExchange).dumpFees();
            uint32[] memory spotIds = spotEngine.getProductIds();
            int128[] memory fees = new int128[](spotIds.length);
            for (uint256 i = 0; i < spotIds.length; i++) {
                fees[i] = sequencerFee[spotIds[i]];
                sequencerFee[spotIds[i]] = 0;
            }
            requireSubaccount(X_ACCOUNT);
            clearinghouse.claimSequencerFees(fees);
```

**File:** core/contracts/EndpointTx.sol (L332-385)
```text
    function submitSlowModeTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );

        // special case for DepositCollateral because upon
        // slow mode submission we must take custody of the
        // actual funds

        address sender = msg.sender;

        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointStorage.sol (L55-55)
```text
    int128 internal slowModeFees;
```

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```

**File:** core/contracts/Endpoint.sol (L1-404)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
import "./interfaces/IEndpoint.sol";
import "./interfaces/IOffchainExchange.sol";
import "./interfaces/clearinghouse/IClearinghouse.sol";
import "./EndpointGated.sol";
import "./EndpointTx.sol";
import "./EndpointStorage.sol";
import "./common/Errors.sol";
import "./libraries/ERC20Helper.sol";
import "./libraries/MathHelper.sol";
import "./interfaces/engine/ISpotEngine.sol";
import "./interfaces/engine/IPerpEngine.sol";
import "./interfaces/IERC20Base.sol";
import "./interfaces/IVerifier.sol";
import "./interfaces/IProxyManager.sol";

// solhint-disable-next-line max-states-count
contract Endpoint is
    EIP712Upgradeable,
    OwnableUpgradeable,
    EndpointStorage,
    IEndpoint
{
    using ERC20Helper for IERC20Base;

    function initialize(
        address _sanctions,
        address _sequencer,
        address _offchainExchange,
        IClearinghouse _clearinghouse,
        address _verifier,
        address _endpointTx
    ) external initializer {
        __Ownable_init();
        __EIP712_init("Nado", "0.0.1");
        sequencer = _sequencer;
        clearinghouse = _clearinghouse;
        offchainExchange = _offchainExchange;
        verifier = IVerifier(_verifier);
        sanctions = ISanctionsList(_sanctions);
        endpointTx = _endpointTx;
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
        slowModeConfig = SlowModeConfig({timeout: 0, txCount: 0, txUpTo: 0});
        priceX18[QUOTE_PRODUCT_ID] = ONE;

        if (nlpPools.length == 0) {
            nlpPools.push(
                NlpPool({
                    poolId: 0,
                    subaccount: N_ACCOUNT,
                    owner: address(0),
                    balanceWeightX18: uint128(ONE)
                })
            );
        }
    }

    function _delegatecallEndpointTx(bytes memory callData)
        internal
        returns (bytes memory)
    {
        require(endpointTx != address(0), "Endpoint Tx not set");
        (bool success, bytes memory result) = endpointTx.delegatecall(callData);
        if (!success) {
            if (result.length == 0) {
                revert();
            }
            // solhint-disable-next-line no-inline-assembly
            assembly {
                revert(add(result, 0x20), mload(result))
            }
        }
        return result;
    }

    function validateSubmissionIdx(uint64 idx) private view {
        require(idx == nSubmissions, ERR_INVALID_SUBMISSION_INDEX);
    }

    function isValidDepositAmount(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount
    ) internal returns (bool) {
        int256 minDepositAmount = MIN_DEPOSIT_AMOUNT;
        if (subaccount != X_ACCOUNT && (subaccountIds[subaccount] == 0)) {
            minDepositAmount = MIN_FIRST_DEPOSIT_AMOUNT;
        }
        return
            clearinghouse.checkMinDeposit(productId, amount, minDepositAmount);
    }

    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
    }

    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

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
    }

    function getNlpPools() external view returns (NlpPool[] memory) {
        return nlpPools;
    }

    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
    }

    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
        }
    }

    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }

    function processSlowModeTransaction(
        address sender,
        bytes calldata transaction
    ) public virtual {
        require(msg.sender == address(this));

        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.processSlowModeTransactionImpl.selector,
                sender,
                transaction
            )
        );
    }

    function processTransaction(bytes calldata transaction) internal virtual {
        TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );
        if (txType == TransactionType.ExecuteSlowMode) {
            SlowModeConfig memory _slowModeConfig = slowModeConfig;
            _executeSlowModeTransaction(_slowModeConfig, true);
            slowModeConfig = _slowModeConfig;
        } else {
            _delegatecallEndpointTx(
                abi.encodeWithSelector(
                    EndpointTx.processTransactionImpl.selector,
                    transaction
                )
            );
        }
    }

    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }

    function submitTransactionsCheckedWithGasLimit(
        uint64 idx,
        bytes[] calldata transactions,
        uint256 gasLimit
    ) external {
        uint256 initialGas = gasleft();
        validateSubmissionIdx(idx);
        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            uint256 gasUsed = initialGas - gasleft();
            if (gasUsed > gasLimit) {
                verifier.revertGasInfo(i, gasUsed);
            }
        }
        verifier.revertGasInfo(transactions.length, initialGas - gasleft());
    }

    function setInitialPrice(uint32 productId, int128 initialPriceX18)
        external
    {
        require(
            msg.sender == address(spotEngine) ||
                msg.sender == address(perpEngine),
            ERR_UNAUTHORIZED
        );
        require(priceX18[productId] == 0, ERR_UNAUTHORIZED);
        priceX18[productId] = initialPriceX18;
    }

    function getSubaccountId(bytes32 subaccount)
        external
        view
        returns (uint64)
    {
        return subaccountIds[subaccount];
    }

    function getPriceX18(uint32 productId)
        public
        override
        returns (int128 _priceX18)
    {
        _priceX18 = priceX18[productId];
        require(_priceX18 != 0, ERR_INVALID_PRODUCT);
        emit PriceQuery(productId);
    }

    function getTime() external view returns (uint128) {
        Times memory t = times;
        uint128 _time = t.spotTime > t.perpTime ? t.spotTime : t.perpTime;
        require(_time != 0, ERR_INVALID_TIME);
        return _time;
    }

    function getOffchainExchange() external view returns (address) {
        return offchainExchange;
    }

    struct AddressSlot {
        address value;
    }

    function _getProxyManager() internal view returns (address) {
        AddressSlot storage proxyAdmin;
        // solhint-disable-next-line no-inline-assembly
        assembly {
            proxyAdmin.slot := 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103
        }
        return proxyAdmin.value;
    }

    function upgradeEndpointTx(address _endpointTx) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        endpointTx = _endpointTx;
    }

    function getEndpointTx() external view returns (address) {
        return endpointTx;
    }

    function getSequencer() external view returns (address) {
        return sequencer;
    }

    function getSlowModeTx(uint64 idx)
        external
        view
        returns (
            SlowModeTx memory,
            uint64,
            uint64
        )
    {
        return (
            slowModeTxs[idx],
            slowModeConfig.txUpTo,
            slowModeConfig.txCount
        );
    }

    function getNonce(address sender) external view returns (uint64) {
        return nonces[sender];
    }
}
```
