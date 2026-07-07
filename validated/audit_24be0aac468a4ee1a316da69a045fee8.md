### Title
Unchecked `transferFrom` Return Value Enables USDC-E Drain from DDA — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` without checking its boolean return value. If the token returns `false` instead of reverting on failure, the function continues to withdraw USDC-E from the target DDA and transfer it to the caller — without the caller ever providing USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no access-control modifier beyond a chain-ID gate (`block.chainid == 57073`). Its purpose is to swap USDC-E held in a Direct Deposit Account (DDA) for USDC: the caller is expected to supply USDC via `transferFrom`, the DDA's USDC-E is withdrawn to `ContractOwner`, and then USDC-E is forwarded to the caller.

The critical step — pulling USDC from the caller — uses a raw interface call whose `bool` return value is silently discarded:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // return value ignored
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));       // withdraws USDC-E to ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);                  // sends USDC-E to caller
``` [1](#0-0) 

The project already has `ERC20Helper.safeTransferFrom` — a safe wrapper that checks both the low-level call success and the decoded boolean — but it is not used here. [2](#0-1) 

`IERC20Base.transferFrom` is declared to return `bool`, matching the ERC-20 standard. [3](#0-2) 

---

### Impact Explanation

If the USDC token at the hardcoded address (`0x2D270e6886d130D724215A266106e6832161EAEd` on chain 57073 / Ink) returns `false` on a failed transfer rather than reverting, an attacker with zero USDC balance or zero allowance can:

1. Call `replaceUsdcEWithUsdc(subaccount)` for any subaccount whose DDA holds USDC-E.
2. The `transferFrom` silently fails (returns `false`), no USDC is pulled from the attacker.
3. `DirectDepositV1.withdraw` moves all USDC-E from the DDA to `ContractOwner`.
4. `safeTransfer` forwards that USDC-E to the attacker.

The attacker receives the full USDC-E balance of the targeted DDA at zero cost. The DDA owner loses their deposited collateral.

---

### Likelihood Explanation

The function is callable by any unprivileged address on chain 57073 — no role, signature, or ownership check beyond the chain-ID gate. The exploitability depends on whether the specific USDC deployment at the hardcoded address returns `false` on failure rather than reverting. Non-standard or bridged stablecoin deployments on newer chains (Ink is a newer L2) do not always follow the revert-on-failure convention. The code provides no protection regardless of token behavior, and the existing `ERC20Helper.safeTransferFrom` helper was available and unused.

---

### Recommendation

Replace the raw `transferFrom` call with the project's own safe wrapper:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` is already imported via `using ERC20Helper for IERC20Base` in `ContractOwner.sol` (inherited through the `using` directive applied to `IERC20Base`), so no new dependency is needed. [4](#0-3) 

---

### Proof of Concept

1. Deploy or identify a DDA (`directDepositV1Address[subaccount]`) on chain 57073 that holds a non-zero USDC-E balance.
2. As an attacker with **zero USDC balance and zero USDC allowance**, call:
   ```solidity
   ContractOwner.replaceUsdcEWithUsdc(subaccount);
   ```
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, balance)` returns `false` — no revert.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes, moving USDC-E to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, balance)` executes, sending USDC-E to the attacker.
6. Attacker receives the full USDC-E balance; the DDA owner's collateral is stolen. [5](#0-4)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-619)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
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
    }
```

**File:** core/contracts/interfaces/IERC20Base.sol (L25-29)
```text
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);
```
