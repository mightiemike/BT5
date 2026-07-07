### Title
Use of Deprecated `draft-EIP712Upgradeable` Produces a Stale Domain Separator, Enabling Cross-Chain Signature Replay on All User-Signed Transactions — (File: `core/contracts/EndpointTx.sol`, `core/contracts/Endpoint.sol`)

---

### Summary

Both `Endpoint` and `EndpointTx` import and inherit from the deprecated `draft-EIP712Upgradeable.sol`. The draft version caches the EIP-712 domain separator at initialization and never recomputes it if the chain ID changes. Because `EndpointTx` is executed via `delegatecall` inside `Endpoint`'s storage context, every user-signed transaction — withdrawals, liquidations, linked-signer assignments, NLP mints/burns, and quote transfers — is hashed against a domain separator that can become permanently stale after a chain fork. A stale separator makes signatures valid on both the original chain and the forked chain simultaneously, enabling cross-chain replay of any signed user operation.

---

### Finding Description

`Endpoint.sol` line 6 imports `draft-EIP712Upgradeable.sol`:

```solidity
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
``` [1](#0-0) 

`EndpointTx.sol` line 5 does the same:

```solidity
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
``` [2](#0-1) 

`package.json` pins `@openzeppelin/contracts-upgradeable` to `^4.8.0-rc.2`, the exact release in which OZ deprecated the draft module and introduced the non-draft `EIP712Upgradeable` that recomputes the domain separator when `block.chainid` differs from the cached value. [3](#0-2) 

The draft version stores the domain separator once during `__EIP712_init("Nado", "0.0.1")` and never refreshes it. [4](#0-3) 

`EndpointTx` is not called directly; it is invoked via `delegatecall` from `Endpoint`, so all execution runs in `Endpoint`'s storage context and reads the stale cached separator. [5](#0-4) 

Every user-signed transaction path in `processTransactionImpl` calls `validateSignedTx`, which calls `_hashTypedDataV4(computeDigest(...))` — directly consuming the cached, potentially stale separator: [6](#0-5) 

Affected transaction types include `WithdrawCollateral`, `WithdrawCollateralV2`, `LiquidateSubaccount`, `LinkSigner`, `TransferQuote`, `MintNlp`, and `BurnNlp`. [7](#0-6) 

---

### Impact Explanation

If the deployment chain undergoes a hard fork that assigns a new chain ID to one branch, the domain separator cached in `Endpoint`'s storage remains tied to the original chain ID on **both** branches. A signature produced by a user on chain A is therefore structurally identical and valid on chain B. An attacker who observes a signed `WithdrawCollateral` or `LinkSigner` transaction on one branch can replay it verbatim on the other branch without any additional privilege. This corrupts:

- **User collateral balances** — replay of a withdrawal drains funds a second time on the forked chain.
- **Linked-signer state** — replay of a `LinkSigner` transaction installs an attacker-controlled signer on the forked chain.
- **Nonce state** — nonces are consumed on both chains by the replayed transaction, preventing the legitimate user from issuing further signed operations on the forked chain. [8](#0-7) 

---

### Likelihood Explanation

The trigger requires a chain fork that changes the chain ID of one branch. This is not a daily occurrence, but it is a documented real-world event (e.g., Ethereum Classic split from Ethereum, EIP-155 replay-protection motivation). The Nado protocol is deployed on a live chain; any contentious upgrade or emergency fork creates the exact precondition. No privileged access, key compromise, or social engineering is required — the attacker only needs to observe a signed transaction on one branch and submit it on the other. Likelihood is **low-to-medium** given the rarity of chain forks, but the impact when triggered is **high**.

---

### Recommendation

Replace the deprecated draft import with the non-draft version in both files:

```solidity
// Before (deprecated)
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";

// After
import "@openzeppelin/contracts-upgradeable/utils/cryptography/EIP712Upgradeable.sol";
```

The non-draft `EIP712Upgradeable` (available in OZ ≥ 4.8.0, which is already the pinned version) recomputes the domain separator whenever `block.chainid` differs from the cached value, eliminating the stale-separator window after any chain fork.

---

### Proof of Concept

1. Nado is deployed on chain ID `C`. `Endpoint.__EIP712_init("Nado", "0.0.1")` caches `domainSeparator = keccak256(abi.encode(..., C, ...))` in storage.
2. The chain forks; one branch retains chain ID `C`, the other adopts chain ID `C'`.
3. On the `C'` branch, `Endpoint`'s storage still holds the separator for `C` because the draft version never recomputes it.
4. User Alice signs a `WithdrawCollateral` transaction on chain `C`. The sequencer includes it; her nonce advances to `n+1` on chain `C`.
5. An attacker submits the identical signed bytes to the sequencer (or directly via `executeSlowModeTransaction`) on chain `C'`. `validateSignedTx` calls `_hashTypedDataV4` → reads the stale separator (still `C`) → the digest matches Alice's signature → nonce check passes (Alice's nonce on `C'` is still `n`) → withdrawal executes, draining Alice's collateral on `C'` as well. [9](#0-8) [10](#0-9)

### Citations

**File:** core/contracts/Endpoint.sol (L6-6)
```text
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
```

**File:** core/contracts/Endpoint.sol (L40-40)
```text
        __EIP712_init("Nado", "0.0.1");
```

**File:** core/contracts/Endpoint.sol (L68-84)
```text
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
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/EndpointTx.sol (L5-5)
```text
import "@openzeppelin/contracts-upgradeable/utils/cryptography/draft-EIP712Upgradeable.sol";
```

**File:** core/contracts/EndpointTx.sol (L72-77)
```text
    function validateNonce(bytes32 sender, uint64 nonce) internal virtual {
        require(
            nonce == nonces[address(uint160(bytes20(sender)))]++,
            ERR_WRONG_NONCE
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L86-106)
```text
    function validateSignedTx(
        bytes32 sender,
        uint64 nonce,
        bytes calldata transaction,
        bytes memory signature,
        bool allowLinkedSigner
    ) internal {
        validateNonce(sender, nonce);
        validateSignature(
            sender,
            _hashTypedDataV4(
                computeDigest(
                    IEndpoint.TransactionType(uint8(transaction[0])),
                    transaction[1:]
                )
            ),
            signature,
            allowLinkedSigner
        );
        requireSubaccount(sender);
    }
```

**File:** core/contracts/EndpointTx.sol (L391-645)
```text
        if (txType == IEndpoint.TransactionType.LiquidateSubaccount) {
            IEndpoint.SignedLiquidateSubaccount memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLiquidateSubaccount)
            );
            if (signedTx.tx.sender != N_ACCOUNT) {
                validateSignedTx(
                    signedTx.tx.sender,
                    signedTx.tx.nonce,
                    transaction,
                    signedTx.signature,
                    true
                );
                // No liquidation fee for finalization (productId == uint32.max) because:
                // 1) The liquidator receives no profit from finalization
                // 2) Finalization can only occur once per underwater subaccount, eliminating
                //    sybil attack concerns that would otherwise require a fee deterrent.
                if (signedTx.tx.productId != type(uint32).max) {
                    chargeFee(signedTx.tx.sender, LIQUIDATION_FEE);
                }
            }
            clearinghouse.liquidateSubaccount(signedTx.tx);
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
        } else if (txType == IEndpoint.TransactionType.SpotTick) {
            IEndpoint.SpotTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.SpotTick)
            );
            Times memory t = times;
            uint128 dt = t.spotTime == 0 ? 0 : txn.time - t.spotTime;
            spotEngine.updateStates(dt);
            t.spotTime = txn.time;
            times = t;
        } else if (txType == IEndpoint.TransactionType.PerpTick) {
            IEndpoint.PerpTick memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.PerpTick)
            );
            Times memory t = times;
            uint128 dt = t.perpTime == 0 ? 0 : txn.time - t.perpTime;
            perpEngine.updateStates(dt, txn.avgPriceDiffs);
            t.perpTime = txn.time;
            times = t;
        } else if (txType == IEndpoint.TransactionType.UpdatePrice) {
            (uint32 productId, int128 newPriceX18) = clearinghouse.updatePrice(
                transaction
            );
            if (productId != 0) {
                priceX18[productId] = newPriceX18;
            }
        } else if (txType == IEndpoint.TransactionType.SettlePnl) {
            clearinghouse.settlePnl(transaction);
        } else if (txType == IEndpoint.TransactionType.MatchOrders) {
            IEndpoint.MatchOrders memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.MatchOrders)
            );
            requireSubaccount(txn.taker.order.sender);
            requireSubaccount(txn.maker.order.sender);

            IEndpoint.MatchOrdersWithSigner memory txnWithSigner = IEndpoint
                .MatchOrdersWithSigner({
                    matchOrders: txn,
                    takerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.taker.order.sender
                    ),
                    makerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.maker.order.sender
                    ),
                    takerAmountDelta: 0
                });
            IOffchainExchange(offchainExchange).matchOrders(txnWithSigner);
        } else if (txType == IEndpoint.TransactionType.MatchOrdersWithAmount) {
            IEndpoint.MatchOrdersWithAmount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.MatchOrdersWithAmount)
            );
            requireSubaccount(txn.matchOrders.taker.order.sender);
            requireSubaccount(txn.matchOrders.maker.order.sender);
            IEndpoint.MatchOrdersWithSigner memory txnWithSigner = IEndpoint
                .MatchOrdersWithSigner({
                    matchOrders: txn.matchOrders,
                    takerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.matchOrders.taker.order.sender
                    ),
                    makerLinkedSigner: getLinkedSignerOrNlpSigner(
                        txn.matchOrders.maker.order.sender
                    ),
                    takerAmountDelta: txn.takerAmountDelta
                });
            IOffchainExchange(offchainExchange).matchOrders(txnWithSigner);
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedMintNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedBurnNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
        } else if (txType == IEndpoint.TransactionType.ManualAssert) {
            clearinghouse.manualAssert(transaction);
        } else if (txType == IEndpoint.TransactionType.LinkSigner) {
            IEndpoint.SignedLinkSigner memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedLinkSigner)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            linkedSigners[signedTx.tx.sender] = address(
                uint160(bytes20(signedTx.tx.signer))
            );
        } else if (txType == IEndpoint.TransactionType.UpdateFeeTier) {
            clearinghouse.updateFeeTier(transaction);
        } else if (txType == IEndpoint.TransactionType.TransferQuote) {
            IEndpoint.SignedTransferQuote memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedTransferQuote)
            );
            _recordSubaccount(signedTx.tx.recipient);
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            if (
                RiskHelper.isIsolatedSubaccount(signedTx.tx.recipient) ||
                RiskHelper.isIsolatedSubaccount(signedTx.tx.sender)
            ) {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE / 10);
            } else {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            }
            clearinghouse.transferQuote(signedTx.tx);
        } else if (txType == IEndpoint.TransactionType.AssertCode) {
            clearinghouse.assertCode(transaction);
        } else if (txType == IEndpoint.TransactionType.AssertProduct) {
            IOffchainExchange(offchainExchange).assertProduct(transaction);
        } else if (
            txType == IEndpoint.TransactionType.CreateIsolatedSubaccount
        ) {
            IEndpoint.CreateIsolatedSubaccount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.CreateIsolatedSubaccount)
            );
            bytes32 newIsolatedSubaccount = IOffchainExchange(offchainExchange)
                .createIsolatedSubaccount(
                    txn,
                    getLinkedSigner(txn.order.sender)
                );
            _recordSubaccount(newIsolatedSubaccount);
        } else if (
            txType == IEndpoint.TransactionType.CloseIsolatedSubaccount
        ) {
            IEndpoint.CloseIsolatedSubaccount memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.CloseIsolatedSubaccount)
            );
            IOffchainExchange(offchainExchange).tryCloseIsolatedSubaccount(
                txn.subaccount
            );
        } else {
            revert();
        }
    }
```

**File:** core/package.json (L23-23)
```json
    "@openzeppelin/contracts-upgradeable": "^4.8.0-rc.2",
```
