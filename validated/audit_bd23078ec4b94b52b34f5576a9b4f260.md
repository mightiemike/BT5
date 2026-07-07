### Title
Unchecked `transferFrom` Return Value Allows USDC-E Drain Without USDC Payment — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` is an unpermissioned external function that is intended to atomically swap USDC-E held in a `DirectDepositV1` address for USDC. It calls the raw ERC20 `transferFrom` to pull USDC from the caller but does not check the return value. If the USDC token returns `false` on failure (insufficient balance or allowance) rather than reverting, the function silently continues, withdraws USDC-E from the `DirectDepositV1` contract, and transfers it to the caller — with zero USDC paid.

---

### Finding Description

`replaceUsdcEWithUsdc` performs a three-step atomic swap:

1. Pull `balance` USDC from `msg.sender` into `directDepositV1`
2. Withdraw `balance` USDC-E from `directDepositV1` to `ContractOwner`
3. Transfer `balance` USDC-E from `ContractOwner` to `msg.sender`

Step 1 uses the raw `IERC20Base.transferFrom` call without checking its boolean return value:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
IERC20Base(usdcE).safeTransfer(msg.sender, balance);
``` [1](#0-0) 

The protocol's own `ERC20Helper.safeTransferFrom` library exists precisely to guard against this — it wraps the call and requires `success && (data.length == 0 || abi.decode(data, (bool)))`: [2](#0-1) 

`replaceUsdcEWithUsdc` bypasses this helper and calls `transferFrom` directly on the raw interface, discarding the return value. Steps 2 and 3 use `safeTransfer`/`withdraw` which do revert on failure, so the USDC-E release is guaranteed to execute even when the USDC pull silently failed.

There is no access control on this function — any address may call it: [3](#0-2) 

---

### Impact Explanation

An attacker who calls `replaceUsdcEWithUsdc` with zero USDC balance or zero allowance, against any `subaccount` whose `directDepositV1` holds USDC-E, will:

- Pay 0 USDC
- Receive the full USDC-E balance of that `directDepositV1` address

This drains USDC-E from every `DirectDepositV1` deposit address in the protocol at no cost, directly analogous to the UniswapV3 pool drain described in the external report. The corrupted asset delta is the full USDC-E balance of every targeted `directDepositV1`.

---

### Likelihood Explanation

The function is restricted to `block.chainid == 57073` (Ink Mainnet). The hardcoded USDC address `0x2D270e6886d130D724215A266106e6832161EAEd` is a bridged/wrapped token on Ink Chain whose exact revert-vs-return-false behavior is not guaranteed to match Circle's canonical USDC. Bridged ERC-20 tokens on L2s frequently follow the older ERC-20 pattern of returning `false` rather than reverting. Any `directDepositV1` address holding USDC-E is a target. The function is callable by any unprivileged address with no preconditions beyond the chain ID check.

---

### Recommendation

Replace the raw `transferFrom` call with the protocol's own `ERC20Helper.safeTransferFrom`, consistent with every other token pull in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [2](#0-1) 

Additionally, add a post-transfer balance assertion to verify the USDC balance of `directDepositV1` increased by exactly `balance` before proceeding with the USDC-E withdrawal.

---

### Proof of Concept

1. Identify any `subaccount` with a non-zero `directDepositV1Address` holding USDC-E (e.g., 10,000 USDC-E).
2. Deploy an attacker EOA with 0 USDC and 0 USDC allowance.
3. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` from the attacker EOA on Ink Mainnet (`chainid == 57073`).
4. If USDC at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed `transferFrom` (rather than reverting):
   - `transferFrom` returns `false`, return value is discarded, execution continues
   - `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers 10,000 USDC-E to `ContractOwner`
   - `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` transfers 10,000 USDC-E to the attacker
5. Attacker receives 10,000 USDC-E having paid 0 USDC. [3](#0-2)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
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
